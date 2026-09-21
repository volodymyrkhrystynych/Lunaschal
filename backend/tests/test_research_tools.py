"""The shared offline-first research toolbox.

The gate is the whole point of this module: the library is searched before the
web, in every surface that can search at all. These tests pin the order and the
two reasons allowed to skip it.
"""
import pytest

from backend.delegate import research_tools


@pytest.fixture
def library(monkeypatch):
    """A library that answers, and a delegate that records being reached."""
    calls = {'search': 0, 'read': 0, 'delegate': []}

    def local(name, args):
        if name == 'local_knowledge_search':
            calls['search'] += 1
            return 'two candidates', {'tool': name, 'ok': True, 'count': 2}
        calls['read'] += 1
        return 'the article', {'tool': name, 'ok': True}

    def run(task, **kw):
        calls['delegate'].append({'task': task, **kw})
        return {'summary': 'what the web said', 'steps': [{'ok': True}],
                'sources': [{'url': 'https://ex.com/a', 'title': 'A'}]}

    monkeypatch.setattr(research_tools.knowledge_tools, 'run_tool', local)
    monkeypatch.setattr(research_tools.agent, 'run', run)
    return calls


def _delegate(tools, reason):
    return tools.run_tool('delegate', {'task': 'how do people do X', 'reason': reason})


# --- The toolbox itself ---

def test_the_toolbox_is_the_library_and_the_delegate():
    names = {t['function']['name'] for t in research_tools.TOOLS}
    assert names == {'local_knowledge_search', 'local_knowledge_read', 'delegate'}
    # No raw web tool is ever offered to a surface: a fetched page belongs in a
    # summary, not in a transcript paid for on every later turn.
    assert not any(n.startswith('web_') for n in names)


def test_every_offered_tool_can_actually_be_dispatched():
    tools, dispatch, _state = research_tools.build()
    assert {t['function']['name'] for t in tools} == set(dispatch)


def test_the_reason_enum_names_the_three_ways_in():
    params = research_tools.DELEGATE_TOOL['function']['parameters']
    assert params['properties']['reason']['enum'] == [
        'local_insufficient', 'current', 'user_requested',
    ]
    assert params['required'] == ['task', 'reason']


# --- The gate ---

def test_the_web_is_refused_before_the_library_is_searched(library):
    _tools, _dispatch, state = research_tools.build()
    text, event = _delegate(state, 'local_insufficient')

    assert event['ok'] is False
    assert event['error'] == 'offline library has not been searched'
    assert 'offline library' in text
    assert library['delegate'] == []


def test_the_web_is_refused_until_the_strongest_hit_is_read(library):
    _tools, _dispatch, state = research_tools.build()
    state.run_tool('local_knowledge_search', {'queries': ['x', 'y']})

    _text, event = _delegate(state, 'local_insufficient')
    assert event['error'] == 'offline search result has not been read'
    assert library['delegate'] == []


def test_the_web_opens_once_the_library_has_been_read(library):
    _tools, _dispatch, state = research_tools.build()
    state.run_tool('local_knowledge_search', {'queries': ['x', 'y']})
    state.run_tool('local_knowledge_read', {'archiveId': 'a', 'path': 'p'})

    text, event = _delegate(state, 'local_insufficient')
    assert event['ok'] is True
    assert text == 'what the web said'
    assert event['sources'] == [{'url': 'https://ex.com/a', 'title': 'A'}]
    assert len(library['delegate']) == 1


def test_a_search_that_found_nothing_does_not_require_a_read(library, monkeypatch):
    """Nothing to read is not the same as refusing to read: a library that came
    back empty has already answered the question it can answer."""
    monkeypatch.setattr(research_tools.knowledge_tools, 'run_tool',
                        lambda name, args: ('nothing', {'tool': name, 'ok': True, 'count': 0}))
    _tools, _dispatch, state = research_tools.build()
    state.run_tool('local_knowledge_search', {'queries': ['x', 'y']})

    _text, event = _delegate(state, 'local_insufficient')
    assert event['ok'] is True


# --- The two reasons that skip it ---

def test_an_inherently_current_question_goes_straight_to_the_web(library):
    _tools, _dispatch, state = research_tools.build()
    _text, event = _delegate(state, 'current')

    assert event['ok'] is True
    assert library['search'] == 0
    assert len(library['delegate']) == 1


def test_asking_for_a_web_search_skips_the_library(library):
    """"Search the web for X" is an instruction, not a lookup strategy to be
    second-guessed — the library is not where the user asked to look."""
    _tools, _dispatch, state = research_tools.build()
    _text, event = _delegate(state, 'user_requested')

    assert event['ok'] is True
    assert library['search'] == 0
    assert len(library['delegate']) == 1


@pytest.mark.parametrize('reason', ['', None, 'because', 'LOCAL_INSUFFICIENT'])
def test_an_unrecognised_reason_is_gated_rather_than_waved_through(library, reason):
    """A reason nobody wrote down must fall on the safe side of the gate."""
    _tools, _dispatch, state = research_tools.build()
    _text, event = state.run_tool('delegate', {'task': 't', 'reason': reason})

    assert event['ok'] is False
    assert library['delegate'] == []


# --- Per-run state ---

def test_two_toolboxes_do_not_share_the_gate(library):
    """`searched`/`read` are per-run facts. A shared instance would let one
    reply's library search unlock the web for the next one."""
    _t1, _d1, first = research_tools.build()
    first.run_tool('local_knowledge_search', {'queries': ['x', 'y']})
    first.run_tool('local_knowledge_read', {'archiveId': 'a', 'path': 'p'})

    _t2, _d2, second = research_tools.build()
    _text, event = _delegate(second, 'local_insufficient')
    assert event['error'] == 'offline library has not been searched'


def test_the_checkpoint_and_deadline_reach_the_delegate(library):
    marker = object()
    _tools, _dispatch, state = research_tools.build(checkpoint=marker, deadline=123.0)
    _delegate(state, 'current')

    assert library['delegate'][0]['checkpoint'] is marker
    assert library['delegate'][0]['deadline'] == 123.0


# --- Failure reporting ---

def test_a_delegate_run_that_found_nothing_is_not_reported_as_ok(monkeypatch):
    monkeypatch.setattr(research_tools.agent, 'run', lambda task, **kw: {
        'summary': '', 'steps': [{'ok': False, 'error': 'search is unavailable'}],
        'sources': [],
    })
    _tools, _dispatch, state = research_tools.build()
    _text, event = _delegate(state, 'current')

    assert event['ok'] is False
    assert event['error'] == 'search is unavailable'
