"""Saving a chapter or a note from more than one device, and what follows from
it: conflicts, history, daily words, offline creates, the phone's bundle, and
chapter proposals."""
import json
import time

import pytest
from ulid import ULID

from backend import day_boundary
from backend.db.connection import get_db
from backend.writing import discuss
from backend.writing.chapters import word_count
from backend.writing.proposals import WritingProposals

KINDS = [('chapters', 'chapters'), ('notes', 'notes')]


def _project(client, title='Salt Roads'):
    return client.post('/api/writing/projects', json={'title': title}).get_json()['id']


def _document(client, pid, kind, title='One', id=None):
    body = {'title': title}
    if id:
        body['id'] = id
    return client.post(f'/api/writing/projects/{pid}/{kind}', json=body).get_json()['id']


def _save(client, kind, id, **body):
    return client.patch(f'/api/writing/{kind}/{id}', json=body)


def _message_with(cid, proposals):
    mid = str(ULID())
    get_db().execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata, created_at) "
        "VALUES (?,?,'assistant','',?,?)",
        (mid, cid, json.dumps({'proposals': proposals}), int(time.time())))
    get_db().commit()
    return mid


def _discussion(client, pid, **body):
    return client.post(f'/api/writing/projects/{pid}/conversations', json=body).get_json()['id']


# --- Revisions and conflicts ---

@pytest.mark.parametrize('kind,_', KINDS)
def test_a_save_moves_the_revision_and_reports_it(client, kind, _):
    pid = _project(client)
    id = _document(client, pid, kind)
    reply = _save(client, kind, id, content='First.', baseRevision=0)
    assert reply.status_code == 200
    assert reply.get_json()['revision'] == 1
    assert client.get(f'/api/writing/{kind}/{id}').get_json()['revision'] == 1


@pytest.mark.parametrize('kind,_', KINDS)
def test_a_stale_save_is_refused_within_the_same_second(client, kind, _):
    """The reason `revision` exists: `updated_at` could not see this."""
    pid = _project(client)
    id = _document(client, pid, kind)
    assert _save(client, kind, id, content='From the desktop.', baseRevision=0).status_code == 200
    stale = _save(client, kind, id, content='From the phone.', baseRevision=0)
    assert stale.status_code == 409
    body = stale.get_json()
    assert body['conflict'] is True
    assert body['current']['content'] == 'From the desktop.'
    assert body['current']['revision'] == 1
    assert client.get(f'/api/writing/{kind}/{id}').get_json()['content'] == 'From the desktop.'


@pytest.mark.parametrize('kind,_', KINDS)
def test_a_lost_reply_retried_is_not_a_conflict(client, kind, _):
    pid = _project(client)
    id = _document(client, pid, kind)
    _save(client, kind, id, content='Sent once.', baseRevision=0)
    retry = _save(client, kind, id, content='Sent once.', baseRevision=0)
    assert retry.status_code == 200
    assert retry.get_json()['revision'] == 1


@pytest.mark.parametrize('kind,_', KINDS)
def test_with_no_base_the_last_write_wins_and_still_moves_the_revision(client, kind, _):
    """An older client keeps working, and every other client can still see it."""
    pid = _project(client)
    id = _document(client, pid, kind)
    _save(client, kind, id, content='One.')
    assert _save(client, kind, id, content='Two.').get_json()['revision'] == 2


@pytest.mark.parametrize('kind,_', KINDS)
def test_a_save_to_a_deleted_document_says_so(client, kind, _):
    pid = _project(client)
    id = _document(client, pid, kind)
    client.delete(f'/api/writing/{kind}/{id}')
    assert _save(client, kind, id, content='Into the void.').status_code == 404


@pytest.mark.parametrize('kind,_', KINDS)
@pytest.mark.parametrize('base', ['1', 1.5, True, -1])
def test_a_malformed_base_is_refused_rather_than_ignored(client, kind, _, base):
    pid = _project(client)
    id = _document(client, pid, kind)
    assert _save(client, kind, id, content='x', baseRevision=base).status_code == 400


def test_pinning_does_not_move_a_notes_revision(client):
    pid = _project(client)
    id = _document(client, pid, 'notes')
    _save(client, 'notes', id, content='Text.', baseRevision=0)
    assert _save(client, 'notes', id, pinned=True).get_json()['revision'] == 1


def test_a_position_change_is_not_a_text_edit(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    assert _save(client, 'chapters', id, position=4).get_json()['revision'] == 0


def test_saving_a_note_tells_clients_the_book_changed(client):
    pid = _project(client)
    id = _document(client, pid, 'notes')
    get_db().execute('UPDATE writing_projects SET updated_at=1 WHERE id=?', (pid,))
    get_db().commit()
    _save(client, 'notes', id, content='Text.')
    row = get_db().execute('SELECT updated_at FROM writing_projects WHERE id=?', (pid,)).fetchone()
    assert row['updated_at'] > 1


# --- Chapter history ---

def _chapter_edit_age(id, seconds):
    get_db().execute('UPDATE writing_chapter_revisions SET created_at=created_at-? WHERE chapter_id=?',
                     (seconds, id))
    get_db().commit()


def test_a_chapter_keeps_its_text_before_an_edit_and_coalesces_autosaves(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    _save(client, 'chapters', id, content='First.')
    _save(client, 'chapters', id, content='Second.')
    revisions = client.get(f'/api/writing/chapters/{id}/revisions').get_json()
    assert [r['content'] for r in revisions] == ['']
    _chapter_edit_age(id, 301)
    _save(client, 'chapters', id, content='Third.')
    revisions = client.get(f'/api/writing/chapters/{id}/revisions').get_json()
    assert [r['content'] for r in revisions] == ['Second.', '']


def test_restoring_a_chapter_moves_its_revision_and_can_be_undone(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    _save(client, 'chapters', id, content='Keep this.')
    _chapter_edit_age(id, 301)
    _save(client, 'chapters', id, content='Oops.')
    keep = client.get(f'/api/writing/chapters/{id}/revisions').get_json()[0]
    restored = client.post(f'/api/writing/chapters/{id}/revisions/{keep["id"]}/restore').get_json()
    assert restored['content'] == 'Keep this.'
    assert restored['revision'] == 3
    latest = client.get(f'/api/writing/chapters/{id}/revisions').get_json()[0]
    assert (latest['source'], latest['content']) == ('restore', 'Oops.')


def test_restoring_a_note_moves_its_revision(client):
    pid = _project(client)
    id = _document(client, pid, 'notes')
    _save(client, 'notes', id, content='Original.')
    revision = client.get(f'/api/writing/notes/{id}/revisions').get_json()[0]
    restored = client.post(f'/api/writing/notes/{id}/revisions/{revision["id"]}/restore').get_json()
    assert restored['revision'] == 2


# --- Offline creates ---

@pytest.mark.parametrize('kind,_', KINDS)
def test_a_create_from_the_phone_keeps_its_id_and_replays_safely(client, kind, _):
    pid = _project(client)
    id = str(ULID())
    assert _document(client, pid, kind, id=id) == id
    _save(client, kind, id, content='Written since.')
    replay = client.post(f'/api/writing/projects/{pid}/{kind}', json={'title': 'One', 'id': id})
    assert replay.status_code == 201
    listed = client.get(f'/api/writing/projects/{pid}/{kind}').get_json()
    assert [row['id'] for row in listed] == [id]
    assert client.get(f'/api/writing/{kind}/{id}').get_json()['content'] == 'Written since.'


@pytest.mark.parametrize('kind,_', KINDS)
def test_a_create_into_a_deleted_book_is_a_404_not_a_server_error(client, kind, _):
    """A 500 would stop the phone's whole pass, on every pass, for one document."""
    reply = client.post(f'/api/writing/projects/{ULID()}/{kind}',
                        json={'title': 'x', 'id': str(ULID())})
    assert reply.status_code == 404


def test_a_client_id_that_is_not_a_ulid_is_refused(client):
    pid = _project(client)
    reply = client.post(f'/api/writing/projects/{pid}/notes', json={'title': 'x', 'id': '../etc'})
    assert reply.status_code == 400


# --- Bundle and order ---

def test_the_bundle_is_a_whole_book_with_revisions(client):
    pid = _project(client)
    first = _document(client, pid, 'chapters', 'First')
    second = _document(client, pid, 'chapters', 'Second')
    note = _document(client, pid, 'notes', 'Lyra')
    _save(client, 'chapters', second, content='Body.')
    _save(client, 'notes', note, content='Keeper.')
    bundle = client.get(f'/api/writing/projects/{pid}/bundle').get_json()
    assert bundle['project']['id'] == pid
    assert [c['id'] for c in bundle['chapters']] == [first, second]
    assert bundle['chapters'][1]['content'] == 'Body.'
    assert bundle['chapters'][1]['revision'] == 1
    assert bundle['notes'][0]['content'] == 'Keeper.'


def test_the_bundle_of_a_missing_book_is_a_404(client):
    assert client.get('/api/writing/projects/nope/bundle').status_code == 404


def test_order_sets_every_position_at_once(client):
    pid = _project(client)
    ids = [_document(client, pid, 'chapters', t) for t in ('A', 'B', 'C')]
    reply = client.put(f'/api/writing/projects/{pid}/chapters/order', json={'ids': ids[::-1]})
    assert reply.status_code == 200
    listed = client.get(f'/api/writing/projects/{pid}/chapters').get_json()
    assert [c['title'] for c in listed] == ['C', 'B', 'A']


def test_order_from_a_stale_list_is_refused(client):
    pid = _project(client)
    ids = [_document(client, pid, 'chapters', t) for t in ('A', 'B')]
    _document(client, pid, 'chapters', 'Added elsewhere')
    reply = client.put(f'/api/writing/projects/{pid}/chapters/order', json={'ids': ids})
    assert reply.status_code == 409


# --- Daily words ---

def test_word_count_splits_on_any_whitespace():
    assert word_count('  one\ttwo\n\nтри  ') == 3
    assert word_count('') == 0
    assert word_count(None) == 0


def _stats(client, pid):
    return client.get(f'/api/writing/projects/{pid}/stats').get_json()


def test_chapter_saves_count_net_words_for_today(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    _save(client, 'chapters', id, content='one two three four')
    assert _stats(client, pid)['todayWords'] == 4
    _save(client, 'chapters', id, content='one two')
    assert _stats(client, pid)['todayWords'] == 2


def test_notes_do_not_count_as_words_written(client):
    pid = _project(client)
    id = _document(client, pid, 'notes')
    _save(client, 'notes', id, content='one two three')
    assert _stats(client, pid)['todayWords'] == 0


def test_words_written_offline_count_on_the_day_they_were_written(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    earlier = int(time.time()) - 3 * 86400
    _save(client, 'chapters', id, content='one two', capturedAt=earlier)
    stats = _stats(client, pid)
    assert stats['todayWords'] == 0
    assert stats['days'] == [{'day': day_boundary.day_key_for(earlier), 'words': 2}]


def test_a_future_captured_at_counts_as_now(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    _save(client, 'chapters', id, content='one', capturedAt=int(time.time()) + 10 * 86400)
    assert _stats(client, pid)['todayWords'] == 1


def test_a_refused_save_counts_no_words(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    _save(client, 'chapters', id, content='one', baseRevision=0)
    _save(client, 'chapters', id, content='one two three', baseRevision=0)
    assert _stats(client, pid)['todayWords'] == 1


def test_a_retried_save_counts_its_words_once(client):
    pid = _project(client)
    id = _document(client, pid, 'chapters')
    _save(client, 'chapters', id, content='one two', baseRevision=0)
    _save(client, 'chapters', id, content='one two', baseRevision=0)
    assert _stats(client, pid)['todayWords'] == 2


def test_the_goal_is_set_cleared_and_validated(client):
    pid = _project(client)
    assert client.patch(f'/api/writing/projects/{pid}', json={'dailyWordGoal': 500}).status_code == 200
    assert _stats(client, pid)['dailyWordGoal'] == 500
    client.patch(f'/api/writing/projects/{pid}', json={'dailyWordGoal': None})
    assert _stats(client, pid)['dailyWordGoal'] is None
    assert client.patch(f'/api/writing/projects/{pid}', json={'dailyWordGoal': 0}).status_code == 400


# --- A discussion opened from a chapter ---

def test_a_discussion_from_a_chapter_names_it_in_the_prompt(client):
    pid = _project(client)
    chapter = _document(client, pid, 'chapters', 'The Storm')
    cid = _discussion(client, pid, chapterId=chapter)
    _toolset, prompt, _pid = discuss.run_config(cid)
    assert 'chapter 1, "The Storm"' in prompt


def test_a_chapter_from_another_book_is_refused(client):
    mine, other = _project(client), _project(client, 'Other')
    chapter = _document(client, other, 'chapters')
    reply = client.post(f'/api/writing/projects/{mine}/conversations', json={'chapterId': chapter})
    assert reply.status_code == 400


def test_a_discussion_outlives_its_chapter(client):
    pid = _project(client)
    chapter = _document(client, pid, 'chapters', 'The Storm')
    cid = _discussion(client, pid, chapterId=chapter)
    client.delete(f'/api/writing/chapters/{chapter}')
    _toolset, prompt, _pid = discuss.run_config(cid)
    assert 'The Storm' not in prompt


# --- A discussion opened from a highlighted passage ---

def test_a_highlighted_passage_is_quoted_and_names_the_discussion(client):
    pid = _project(client)
    chapter = _document(client, pid, 'chapters', 'The Storm')
    passage = 'The wind took the lamp and the sea took the wind, and the keeper did not move at all.'
    cid = _discussion(client, pid, chapterId=chapter, passage=passage)
    _toolset, prompt, _pid = discuss.run_config(cid)
    assert f'<<<\n{passage}\n>>>' in prompt
    assert 'replace_passage' in prompt
    title = get_db().execute('SELECT title FROM conversations WHERE id=?', (cid,)).fetchone()['title']
    assert title == 'Rewrite: The wind took the lamp and the sea took…'


def test_a_discussion_without_a_passage_quotes_nothing(client):
    pid = _project(client)
    chapter = _document(client, pid, 'chapters', 'The Storm')
    _toolset, prompt, _pid = discuss.run_config(_discussion(client, pid, chapterId=chapter))
    assert '<<<' not in prompt


@pytest.mark.parametrize('body', [
    {'passage': 'No chapter to come from.'},
    {'passage': '   '},
    {'passage': 7},
    {'passage': 'x' * 6001},
])
def test_a_passage_needs_text_and_its_chapter(client, body):
    pid = _project(client)
    if body['passage'] != 'No chapter to come from.':
        body = {**body, 'chapterId': _document(client, pid, 'chapters')}
    assert client.post(f'/api/writing/projects/{pid}/conversations', json=body).status_code == 400


def test_an_explicit_title_beats_the_passage(client):
    pid = _project(client)
    chapter = _document(client, pid, 'chapters')
    cid = _discussion(client, pid, chapterId=chapter, passage='Some words.', title='Pacing')
    assert get_db().execute('SELECT title FROM conversations WHERE id=?', (cid,)).fetchone()['title'] == 'Pacing'


# --- Chapter proposals ---

def _chapter_with(client, pid, content, title='The Storm'):
    id = _document(client, pid, 'chapters', title)
    _save(client, 'chapters', id, content=content)
    return id


def test_a_chapter_cannot_be_rewritten_whole(client):
    pid = _project(client)
    _chapter_with(client, pid, 'Text.')
    text, event = WritingProposals(pid).run_tool(
        'propose_chapter_update', {'chapter': 'The Storm', 'mode': 'replace', 'content': 'All new.'})
    assert event['ok'] is False
    assert 'rewritten' in text


def test_a_passage_must_appear_exactly_once_to_be_proposed(client):
    pid = _project(client)
    _chapter_with(client, pid, 'The light. The light.')
    for find, ok in (('absent', False), ('The light.', False), ('light. The', True)):
        _text, event = WritingProposals(pid).run_tool('propose_chapter_update', {
            'chapter': 'The Storm', 'mode': 'replace_passage', 'find': find, 'content': 'New.'})
        assert event['ok'] is ok, find


def test_a_proposal_cannot_reach_another_books_chapter(client):
    mine, other = _project(client), _project(client, 'Other')
    _chapter_with(client, other, 'Text.')
    _text, event = WritingProposals(mine).run_tool(
        'propose_chapter_update', {'chapter': 'The Storm', 'content': 'New.'})
    assert event['ok'] is False


def _card(chapter, mode='append', content='New text.', find=''):
    return {'id': 'p1', 'kind': 'writing_chapter_update', 'status': 'pending', 'data': {
        'chapterId': chapter, 'title': 'The Storm', 'mode': mode, 'find': find,
        'content': content, 'reason': ''}}


def _accept(client, mid, data=None):
    body = {'action': 'accept'}
    if data is not None:
        body['data'] = data
    return client.post(f'/api/chat/proposals/{mid}/p1', json=body)


def test_accepting_an_append_keeps_the_text_and_leaves_a_revision(client):
    pid = _project(client)
    chapter = _chapter_with(client, pid, 'Original.')
    mid = _message_with(_discussion(client, pid), [_card(chapter)])
    assert _accept(client, mid).status_code == 200
    row = client.get(f'/api/writing/chapters/{chapter}').get_json()
    assert row['content'] == 'Original.\n\nNew text.'
    assert row['revision'] == 2
    latest = client.get(f'/api/writing/chapters/{chapter}/revisions').get_json()[0]
    assert (latest['source'], latest['content']) == ('accept', 'Original.')


def test_accepting_a_passage_changes_only_that_passage(client):
    pid = _project(client)
    chapter = _chapter_with(client, pid, 'Before. Dull line. After.')
    mid = _message_with(_discussion(client, pid),
                        [_card(chapter, 'replace_passage', 'Sharp line.', 'Dull line.')])
    assert _accept(client, mid).status_code == 200
    assert client.get(f'/api/writing/chapters/{chapter}').get_json()['content'] == 'Before. Sharp line. After.'


def test_typing_elsewhere_in_the_chapter_does_not_stale_a_card(client):
    pid = _project(client)
    chapter = _chapter_with(client, pid, 'Dull line.')
    mid = _message_with(_discussion(client, pid),
                        [_card(chapter, 'replace_passage', 'Sharp line.', 'Dull line.')])
    _save(client, 'chapters', chapter, content='Dull line. And more since.')
    assert _accept(client, mid).status_code == 200
    assert client.get(f'/api/writing/chapters/{chapter}').get_json()['content'] == 'Sharp line. And more since.'


@pytest.mark.parametrize('now_reads', ['It is gone.', 'Dull line. Dull line.'])
def test_a_passage_that_moved_is_refused_and_the_card_stays_pending(client, now_reads):
    pid = _project(client)
    chapter = _chapter_with(client, pid, 'Dull line.')
    mid = _message_with(_discussion(client, pid),
                        [_card(chapter, 'replace_passage', 'Sharp line.', 'Dull line.')])
    _save(client, 'chapters', chapter, content=now_reads)
    assert _accept(client, mid).status_code == 400
    assert client.get(f'/api/writing/chapters/{chapter}').get_json()['content'] == now_reads
    meta = json.loads(get_db().execute('SELECT metadata FROM messages WHERE id=?', (mid,)).fetchone()['metadata'])
    assert meta['proposals'][0]['status'] == 'pending'


def test_an_edited_card_cannot_reach_another_books_chapter(client):
    mine, other = _project(client), _project(client, 'Other')
    theirs = _chapter_with(client, other, 'Theirs.')
    mid = _message_with(_discussion(client, mine), [_card('placeholder')])
    assert _accept(client, mid, _card(theirs)['data']).status_code == 400
    assert client.get(f'/api/writing/chapters/{theirs}').get_json()['content'] == 'Theirs.'


def test_accepted_text_is_not_counted_as_words_written(client):
    pid = _project(client)
    chapter = _chapter_with(client, pid, 'one')
    mid = _message_with(_discussion(client, pid), [_card(chapter, content='a b c d e')])
    _accept(client, mid)
    assert _stats(client, pid)['todayWords'] == 1


def test_a_new_note_card_uses_the_revision_rather_than_the_clock(client):
    """Accept straight after an edit in the same second, which `updated_at`
    cannot distinguish from no edit at all."""
    pid = _project(client)
    note = _document(client, pid, 'notes', 'Lyra')
    _save(client, 'notes', note, content='Keeper.')
    _text, event = WritingProposals(pid).run_tool(
        'propose_note_update', {'note': 'Lyra', 'content': 'Forty years.'})
    assert event['proposal']['data']['baseRevision'] == 1
    row = get_db().execute('SELECT updated_at FROM writing_context_docs WHERE id=?', (note,)).fetchone()
    mid = _message_with(_discussion(client, pid), [{
        'id': 'p1', 'kind': 'writing_note_update', 'status': 'pending', 'data': {
            'noteId': note, 'title': 'Lyra', 'docType': 'note', 'mode': 'append',
            'content': 'Forty years.', 'reason': '', 'baseUpdatedAt': row['updated_at'],
            'baseRevision': 1}}])
    _save(client, 'notes', note, content='Keeper, edited.')
    assert _accept(client, mid).status_code == 400
