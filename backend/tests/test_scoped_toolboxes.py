"""Which surface gets which recall tools, and what it takes to acquire them.

Each toolbox is scoped by an instance bound at mount time, so the questions
worth testing are all about mounting: does a surface with no scope get the
tools anyway, can one surface acquire another's, and does every offered tool
have somewhere to dispatch to.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.delegate import chat as delegate_chat
from backend.research import discuss

pytestmark = pytest.mark.usefixtures('client')

RESEARCH_NAMES = {'local_knowledge_search', 'local_knowledge_read', 'delegate'}
WRITING_NAMES = {'writing_list', 'writing_read', 'writing_search'}
IDEA_NAMES = {'idea_list', 'idea_read', 'idea_search'}


def _names(tools):
    return {t['function']['name'] for t in tools}


def _project(title='The Salt Roads'):
    now = int(time.time())
    pid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_projects(id, title, description, created_at,'
        ' updated_at) VALUES (?,?,?,?,?)',
        (pid, title, '', now, now),
    )
    get_db().commit()
    return pid


def _idea(repo_id=None, title='An idea'):
    now = int(time.time())
    iid = str(ULID())
    get_db().execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, repo_id,'
        " created_at, updated_at) VALUES (?,?,'','','new',?,?,?)",
        (iid, title, repo_id, now, now),
    )
    get_db().commit()
    return iid


# --- the Ideas discussion ---------------------------------------------------

def test_a_discussion_with_no_idea_gets_no_backlog_tools():
    """The existing callers destructure this positionally and assert today's
    names; adding a keyword argument must not change what they see."""
    tools, dispatch, _code = discuss.build_toolbox(None)

    assert not (_names(tools) & IDEA_NAMES)
    assert _names(tools) == set(dispatch)


def test_naming_the_idea_mounts_the_rest_of_its_backlog():
    idea_id = _idea(title='Scoped recall tools')
    tools, dispatch, _code = discuss.build_toolbox(None, idea_id=idea_id)

    assert IDEA_NAMES <= _names(tools)
    assert RESEARCH_NAMES <= _names(tools)
    assert _names(tools) == set(dispatch)


def test_an_idea_that_does_not_exist_mounts_nothing():
    """An unresolvable scope offers no tool at all — the same rule the code
    tools follow when a checkout has vanished."""
    tools, dispatch, _code = discuss.build_toolbox(None, idea_id='01NOPE')

    assert not (_names(tools) & IDEA_NAMES)
    assert _names(tools) == set(dispatch)


def test_the_backlog_tools_are_fresh_per_discussion():
    first = _idea(title='First')
    second = _idea(title='Second')

    a = discuss.build_toolbox(None, idea_id=first)[1]['idea_list']
    b = discuss.build_toolbox(None, idea_id=second)[1]['idea_list']

    assert a is not b
    assert a.exclude_idea_id == first
    assert b.exclude_idea_id == second


def test_the_prompt_mentions_the_backlog_only_when_it_is_reachable():
    assert 'idea_list' in discuss.system_prompt(has_repo=True, has_ideas=True)
    assert 'idea_list' not in discuss.system_prompt(has_repo=True)
    assert 'idea_list' not in discuss.system_prompt(has_repo=False)


# --- the Writing discussion -------------------------------------------------

def _box(toolset, **kwargs):
    return delegate_chat._toolbox(
        toolset, conversation_id=None, checkpoint=None, deadline=None, **kwargs)


def test_a_research_run_with_no_project_is_research_alone():
    tools, dispatch, note = _box('research')

    assert _names(tools) == RESEARCH_NAMES
    assert _names(tools) == set(dispatch)
    assert note == delegate_chat.RESEARCH_TURN_NOTE


def test_naming_a_project_mounts_its_chapters_and_notes():
    tools, dispatch, note = _box('research', writing_project_id=_project())

    assert _names(tools) == RESEARCH_NAMES | WRITING_NAMES
    assert _names(tools) == set(dispatch)
    assert delegate_chat.RESEARCH_TURN_NOTE in note
    assert delegate_chat.WRITING_RECALL_NOTE in note


def test_the_chat_tab_cannot_acquire_a_projects_chapters():
    """The toolset gate, not the field: a body carrying a project id on a chat
    turn is a client bug, and must not widen what that turn can read."""
    tools, _dispatch, _note = _box('chat', writing_project_id=_project())

    assert not (_names(tools) & WRITING_NAMES)


def test_a_silent_caller_stays_silent():
    assert _box('none', writing_project_id=_project()) is None


def test_the_writing_tools_are_fresh_per_run():
    first, second = _project('One'), _project('Two')

    a = _box('research', writing_project_id=first)[1]['writing_list']
    b = _box('research', writing_project_id=second)[1]['writing_list']

    assert a is not b
    assert (a.project_id, b.project_id) == (first, second)


# --- the request field ------------------------------------------------------

def _scope(body, toolset):
    from backend.routes.chat import _writing_scope_for
    return _writing_scope_for(body, toolset)


def test_a_real_project_on_a_research_turn_is_the_scope():
    pid = _project()
    assert _scope({'writingProjectId': pid}, 'research') == pid


def test_a_project_that_does_not_exist_is_dropped():
    """Not for safety — the id is only ever bound into `project_id = ?` — but
    so the prompt does not promise tools that can only answer "nothing found"."""
    assert _scope({'writingProjectId': '01NOPE'}, 'research') is None


def test_the_scope_is_ignored_off_the_research_turn():
    pid = _project()
    assert _scope({'writingProjectId': pid}, 'chat') is None
    assert _scope({'writingProjectId': pid}, 'none') is None


def test_a_missing_field_is_simply_no_scope():
    assert _scope({}, 'research') is None
    assert _scope({'writingProjectId': '  '}, 'research') is None


def test_the_route_hands_the_scope_to_the_reply(client, monkeypatch):
    seen = {}

    def fake(messages, system_prompt, toolset='chat', writing_project_id=None):
        seen['toolset'] = toolset
        seen['project'] = writing_project_id
        yield ('content', 'ok')

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', fake)
    pid = _project()

    client.post('/api/chat/stream', json={
        'messages': [{'role': 'user', 'content': 'what have I written about salt'}],
        'systemPrompt': 'You help me write.',
        'toolset': 'research',
        'writingProjectId': pid,
    })

    assert seen == {'toolset': 'research', 'project': pid}
