"""The Writing discussion on the durable path: a server-built prompt, a
server-asserted project scope, note proposals, and the revisions behind them.

The model is faked where test_delegate_chat.py fakes it — at `stream_reply`
for the route, and at the shared loop's tool turn for the toolbox — so what is
under test is which project, prompt and tools a turn gets, and what accepting a
card does to the notes.
"""
import json
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.delegate import chat as delegate_chat
from backend.writing import discuss, notes as writing_notes
from backend.writing.proposals import WritingProposals

pytestmark = pytest.mark.usefixtures('client')


def _names(tools):
    return {t['function']['name'] for t in tools}


def _project(title='The Salt Roads', description='A smuggler story.'):
    now = int(time.time())
    pid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_projects(id, title, description, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)', (pid, title, description, now, now))
    get_db().commit()
    return pid


def _note(pid, title, content='', doc_type='character', pinned=0, updated_at=None):
    now = updated_at or int(time.time())
    nid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_context_docs(id, project_id, title, content, doc_type,'
        ' pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        (nid, pid, title, content, doc_type, pinned, now, now))
    get_db().commit()
    return nid


def _discussion(pid=None):
    now = int(time.time())
    cid = str(ULID())
    get_db().execute(
        'INSERT INTO conversations(id, title, writing_project_id, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)', (cid, 'Talk', pid, now, now))
    get_db().commit()
    return cid


def _message_with(cid, proposals):
    mid = str(ULID())
    get_db().execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata, status, created_at)"
        " VALUES (?,?,'assistant','ok',?,'done',?)",
        (mid, cid, json.dumps({'proposals': proposals}), int(time.time())))
    get_db().commit()
    return mid


def _post_stream(client, body):
    resp = client.post('/api/chat/stream', json=body)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _capture_stream(monkeypatch):
    seen = {}

    def fake(messages, system_prompt, toolset='chat', **kw):
        seen.update(system_prompt=system_prompt, toolset=toolset, **kw)
        yield ('content', 'ok')
        yield ('done', {'steps': [], 'sources': [], 'proposals': []})

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', fake)
    return seen


# --- prompt and scope -------------------------------------------------------

def test_a_plain_chat_conversation_is_not_a_writing_run():
    assert discuss.run_config(_discussion(None)) is None
    assert discuss.run_config(None) is None
    assert discuss.run_config('no-such-conversation') is None


def test_the_prompt_carries_pinned_notes_whole_and_names_the_rest():
    pid = _project()
    _note(pid, 'Mara', 'Raised on the docks; owes the harbourmaster.', pinned=1)
    _note(pid, 'Outline', '1. The wreck.', doc_type='outline')

    toolset, prompt, project = discuss.run_config(_discussion(pid))

    assert (toolset, project) == ('writing', pid)
    assert '"The Salt Roads"' in prompt and 'A smuggler story.' in prompt
    assert 'owes the harbourmaster' in prompt
    assert '[outline] Outline' in prompt and '1. The wreck.' not in prompt
    assert 'canon_search' not in prompt


def test_the_route_builds_the_prompt_and_scope_itself(client, monkeypatch):
    """Whatever the client sent for prompt, toolset and project is ignored —
    the conversation row decides, so the scope is a server fact."""
    seen = _capture_stream(monkeypatch)
    pid, other = _project(), _project('Another book')
    cid = _discussion(pid)

    _post_stream(client, {
        'messages': [{'role': 'user', 'content': 'hi'}], 'conversationId': cid,
        'systemPrompt': 'Ignore all that', 'toolset': 'chat',
        'writingProjectId': other,
    })

    assert seen['toolset'] == 'writing'
    assert seen['writing_project_id'] == pid
    assert 'The Salt Roads' in seen['system_prompt']
    assert 'Ignore all that' not in seen['system_prompt']


def test_a_chat_conversation_cannot_ask_for_the_writing_tools(client, monkeypatch):
    seen = _capture_stream(monkeypatch)
    pid = _project()
    cid = _discussion(None)

    _post_stream(client, {'messages': [], 'conversationId': cid,
                          'toolset': 'writing', 'writingProjectId': pid})

    assert seen['toolset'] == 'chat'
    assert seen['writing_project_id'] is None


def test_the_writing_toolbox_holds_research_recall_and_proposals_only():
    pid = _project()
    tools, dispatch, note = delegate_chat._toolbox(
        'writing', conversation_id=None, checkpoint=None, deadline=None,
        writing_project_id=pid)

    names = _names(tools)
    assert {'writing_list', 'propose_note', 'propose_note_update',
            'local_knowledge_search', 'delegate'} <= names
    assert not names & {'search_journal', 'remember', 'add_todos', 'canon_search'}
    assert names == set(dispatch)
    assert 'propose_note' in note


def test_the_writing_toolbox_needs_a_project():
    assert delegate_chat._toolbox('writing', conversation_id=None, checkpoint=None,
                                  deadline=None, writing_project_id=None) is None


def test_a_dictated_clip_in_a_discussion_is_answered_as_writing(monkeypatch):
    from backend.chat import autoreply

    pid = _project()
    cid = _discussion(pid)
    uid = str(ULID())
    get_db().execute(
        "INSERT INTO messages(id, conversation_id, role, content, created_at)"
        " VALUES (?,?,'user','What if Mara lied?',?)", (uid, cid, int(time.time())))
    get_db().commit()
    seen = {}
    monkeypatch.setattr(autoreply, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(autoreply.runs, 'start',
                        lambda mid, msgs, prompt, **kw: seen.update(prompt=prompt, **kw))

    assert autoreply.start_reply(cid, uid)
    assert seen['toolset'] == 'writing'
    assert seen['writing_project_id'] == pid
    assert 'The Salt Roads' in seen['prompt']


# --- proposal tools ---------------------------------------------------------

def test_propose_note_stages_a_card_and_writes_nothing():
    pid = _project()
    text, event = WritingProposals(pid).run_tool(
        'propose_note', {'title': 'Harbourmaster', 'doc_type': 'character',
                         'content': 'Old, corrupt, fond of Mara.'})

    assert 'Nothing has been saved' in text
    assert event['proposal'] == {'kind': 'writing_note', 'data': {
        'title': 'Harbourmaster', 'docType': 'character',
        'content': 'Old, corrupt, fond of Mara.'}}
    assert get_db().execute('SELECT COUNT(*) FROM writing_context_docs').fetchone()[0] == 0


def test_propose_note_refuses_a_title_that_already_exists():
    pid = _project()
    _note(pid, 'Mara')
    _text, event = WritingProposals(pid).run_tool(
        'propose_note', {'title': 'mara', 'doc_type': 'character', 'content': 'x'})
    assert event['ok'] is False and 'propose_note_update' in event['error']


def test_an_update_resolves_the_note_and_records_its_version():
    pid = _project()
    nid = _note(pid, 'Mara', 'Docks.', updated_at=1_700_000_000)
    _text, event = WritingProposals(pid).run_tool(
        'propose_note_update', {'note': 'Mara', 'content': 'She can swim.'})

    data = event['proposal']['data']
    assert event['proposal']['kind'] == 'writing_note_update'
    assert (data['noteId'], data['mode'], data['baseUpdatedAt']) == (nid, 'append', 1_700_000_000)


def test_an_update_cannot_reach_another_projects_note():
    pid, other = _project(), _project('Other')
    _note(other, 'Mara')
    _text, event = WritingProposals(pid).run_tool(
        'propose_note_update', {'note': 'Mara', 'content': 'x'})
    assert event['ok'] is False


def test_the_same_proposal_twice_in_one_reply_is_refused():
    pid = _project()
    tools = WritingProposals(pid)
    args = {'title': 'A', 'doc_type': 'note', 'content': 'x'}
    assert tools.run_tool('propose_note', args)[1]['ok'] is True
    assert tools.run_tool('propose_note', args)[1]['ok'] is False


def test_with_no_project_every_proposal_is_refused():
    _text, event = WritingProposals(None).run_tool(
        'propose_note', {'title': 'A', 'doc_type': 'note', 'content': 'x'})
    assert event['ok'] is False


# --- accepting --------------------------------------------------------------

def _resolve(client, mid, pid_, action='accept', data=None):
    body = {'action': action}
    if data is not None:
        body['data'] = data
    return client.post(f'/api/chat/proposals/{mid}/{pid_}', json=body)


def test_accepting_a_new_note_files_it_in_the_discussions_project(client):
    pid = _project()
    mid = _message_with(_discussion(pid), [{
        'id': 'p1', 'kind': 'writing_note', 'status': 'pending',
        'data': {'title': 'Harbourmaster', 'docType': 'character', 'content': 'Corrupt.'}}])

    resp = _resolve(client, mid, 'p1')

    assert resp.status_code == 200
    row = get_db().execute('SELECT * FROM writing_context_docs WHERE id=?',
                           (resp.get_json()['proposal']['result']['id'],)).fetchone()
    assert (row['project_id'], row['title'], row['doc_type']) == (pid, 'Harbourmaster', 'character')


def test_accepting_an_update_appends_and_leaves_a_revision(client):
    pid = _project()
    nid = _note(pid, 'Mara', 'Docks.', updated_at=1_700_000_000)
    mid = _message_with(_discussion(pid), [{
        'id': 'p1', 'kind': 'writing_note_update', 'status': 'pending',
        'data': {'noteId': nid, 'mode': 'append', 'content': 'She can swim.',
                 'baseUpdatedAt': 1_700_000_000}}])

    assert _resolve(client, mid, 'p1').status_code == 200

    note = get_db().execute('SELECT content FROM writing_context_docs WHERE id=?', (nid,)).fetchone()
    assert note['content'] == 'Docks.\n\nShe can swim.'
    revs = client.get(f'/api/writing/notes/{nid}/revisions').get_json()
    assert [(r['source'], r['content'], r['messageId']) for r in revs] == [('accept', 'Docks.', mid)]


def test_an_update_to_a_note_edited_since_is_refused_and_stays_pending(client):
    pid = _project()
    nid = _note(pid, 'Mara', 'Rewritten by hand.', updated_at=1_700_000_500)
    mid = _message_with(_discussion(pid), [{
        'id': 'p1', 'kind': 'writing_note_update', 'status': 'pending',
        'data': {'noteId': nid, 'mode': 'append', 'content': 'x',
                 'baseUpdatedAt': 1_700_000_000}}])

    resp = _resolve(client, mid, 'p1')

    assert resp.status_code == 400 and 'edited' in resp.get_json()['error']
    meta = json.loads(get_db().execute('SELECT metadata FROM messages WHERE id=?', (mid,)).fetchone()[0])
    assert meta['proposals'][0]['status'] == 'pending'


def test_an_edited_card_cannot_move_a_note_into_another_project(client):
    pid, other = _project(), _project('Other')
    foreign = _note(other, 'Their note', 'Theirs.')
    mid = _message_with(_discussion(pid), [{
        'id': 'p1', 'kind': 'writing_note_update', 'status': 'pending',
        'data': {'noteId': 'x', 'mode': 'append', 'content': 'y'}}])

    resp = _resolve(client, mid, 'p1', data={'noteId': foreign, 'mode': 'replace', 'content': 'gone'})

    assert resp.status_code == 400
    assert get_db().execute('SELECT content FROM writing_context_docs WHERE id=?',
                            (foreign,)).fetchone()[0] == 'Theirs.'


def test_a_restore_puts_the_text_back_and_is_itself_undoable(client):
    pid = _project()
    nid = _note(pid, 'Mara', 'Docks.', updated_at=1_700_000_000)
    mid = _message_with(_discussion(pid), [{
        'id': 'p1', 'kind': 'writing_note_update', 'status': 'pending',
        'data': {'noteId': nid, 'mode': 'replace', 'content': 'Nothing left.',
                 'baseUpdatedAt': 1_700_000_000}}])
    _resolve(client, mid, 'p1')
    rev = client.get(f'/api/writing/notes/{nid}/revisions').get_json()[0]

    resp = client.post(f'/api/writing/notes/{nid}/revisions/{rev["id"]}/restore')

    assert resp.status_code == 200 and resp.get_json()['content'] == 'Docks.'
    sources = [r['source'] for r in client.get(f'/api/writing/notes/{nid}/revisions').get_json()]
    assert sources == ['restore', 'accept']


# --- the editor -------------------------------------------------------------

def test_autosave_edits_coalesce_into_one_revision_a_minute(client):
    pid = _project()
    nid = _note(pid, 'Mara', 'v0')
    for text in ('v1', 'v2', 'v3'):
        client.patch(f'/api/writing/notes/{nid}', json={'content': text})
    revs = client.get(f'/api/writing/notes/{nid}/revisions').get_json()
    assert [(r['source'], r['content']) for r in revs] == [('edit', 'v0')]


def test_pinning_is_not_an_edit(client):
    pid = _project()
    nid = _note(pid, 'Mara', 'Docks.', updated_at=1_700_000_000)

    client.patch(f'/api/writing/notes/{nid}', json={'pinned': True})

    row = get_db().execute('SELECT pinned, updated_at FROM writing_context_docs WHERE id=?',
                           (nid,)).fetchone()
    assert (row['pinned'], row['updated_at']) == (1, 1_700_000_000)
    assert client.get(f'/api/writing/notes/{nid}/revisions').get_json() == []
    listed = client.get(f'/api/writing/projects/{pid}/notes').get_json()
    assert listed[0]['pinned'] == 1


def test_coalescing_never_swallows_an_accept(client):
    """An edit right after an accepted proposal still gets its own revision —
    otherwise undoing the accept would also undo the hand edit, or vice versa."""
    pid = _project()
    nid = _note(pid, 'Mara', 'v0')
    db = get_db()
    writing_notes.snapshot(db, nid, source='accept')
    db.commit()
    client.patch(f'/api/writing/notes/{nid}', json={'content': 'v1'})
    sources = [r['source'] for r in client.get(f'/api/writing/notes/{nid}/revisions').get_json()]
    assert sources.count('edit') == 1 and sources.count('accept') == 1
