"""The delegate loop.

The model is a scripted fake throughout: what's under test is how the loop
collects proposals, what it hands back to the main chat model as a summary, and
that it inherits the shared loop's budget and truncation behaviour rather than
re-implementing them.
"""
import json
from types import SimpleNamespace

import pytest

from backend.ai import priority
from backend.delegate import agent
from backend.research import agent as shared


def _call(name, arguments='{}', call_id='c1'):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _script(monkeypatch, responses, finish_reasons=None):
    """Feed the loop a fixed sequence of assistant messages, matching the
    convention in test_research_agent.py."""
    calls = []

    def fake(messages, tools, max_tokens=None):
        calls.append({'messages': list(messages), 'tools': tools})
        i = min(len(calls) - 1, len(responses) - 1)
        reason = (finish_reasons or [])[i] if finish_reasons and i < len(finish_reasons) else None
        if reason is None:
            reason = 'tool_calls' if responses[i].tool_calls else 'stop'
        return responses[i], reason

    monkeypatch.setattr(shared, 'chat_tool_turn', fake)
    return calls


@pytest.fixture(autouse=True)
def clean_gate():
    priority.reset()
    yield
    priority.reset()


def test_a_proposal_is_collected_off_the_step_event(monkeypatch):
    """Proposals come from what the tools actually staged, never parsed back
    out of the model's prose — the same stance research/agent.py takes about
    recording sources from what was fetched."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('propose_task', json.dumps({'title': 'Call the dentist'}))]),
        _msg(content='Staged the to-do.'),
    ])
    result = agent.run('add call the dentist to my todos')

    assert result['proposals'] == [
        {'kind': 'task', 'data': {'title': 'Call the dentist', 'list': 'todo'}}
    ]
    assert result['summary'] == 'Staged the to-do.'


def test_a_refused_proposal_is_not_collected(monkeypatch):
    _script(monkeypatch, [
        _msg(tool_calls=[_call('propose_calorie_log',
                               json.dumps({'description': 'burger'}))]),
        _msg(content='I could not stage that without a calorie count.'),
    ])
    result = agent.run('I ate a burger')

    assert result['proposals'] == []
    assert result['steps'][0]['ok'] is False


def test_steps_stream_before_the_result(monkeypatch):
    """The SSE route needs each step the moment its call finishes; with only a
    blocking form the events all arrive after the run, which is the silent
    spinner they exist to replace."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('propose_flashcards', json.dumps({'topic': 'FSRS'}))]),
        _msg(content='Done.'),
    ])
    kinds = [kind for kind, _ in agent.run_events('quiz me on FSRS')]
    assert kinds == ['step', 'result']


def test_a_truncated_run_does_not_pass_off_a_half_sentence_as_its_summary(monkeypatch):
    """A turn cut off at the token ceiling arrives with no tool calls, exactly
    like a finished one. Handing that fragment to the main model as the summary
    is how the reply ends up describing work that never completed."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('propose_task', json.dumps({'title': 'Buy milk'}))]),
        _msg(content='I staged the to-do and then went on to'),
    ], finish_reasons=['tool_calls', 'length'])
    result = agent.run('remind me to buy milk')

    assert result['truncated'] is True
    assert 'went on to' not in result['summary']
    # The proposal still survives — it was staged before the turn was cut off.
    assert result['proposals'][0]['data']['title'] == 'Buy milk'
    assert 'Buy milk' in result['summary']


def test_a_run_that_did_nothing_says_so(monkeypatch):
    """An empty summary is one the main model will paper over with a guess."""
    _script(monkeypatch, [_msg(content='')], finish_reasons=['stop'])
    assert agent.run('...')['summary'] == 'The delegate could not do anything with that task.'


def test_the_model_is_offered_both_proposal_and_web_tools(monkeypatch):
    calls = _script(monkeypatch, [_msg(content='Nothing to do.')])
    agent.run('hello')

    offered = {t['function']['name'] for t in calls[0]['tools']}
    assert 'propose_task' in offered
    assert 'web_search' in offered


def test_every_offered_tool_can_actually_be_dispatched():
    """A tool in ALL_TOOLS with no dispatch entry answers "Unknown tool", which
    reads to the model as a broken tool rather than one it should not call."""
    offered = {t['function']['name'] for t in agent.ALL_TOOLS}
    assert offered == set(agent.DISPATCH)


def test_the_turn_budget_is_tighter_than_a_background_research_pass():
    """A delegate run sits between Enter and the first token — its worst case is
    latency the user is watching, unlike the nightly research worker."""
    assert agent.MAX_TOOL_TURNS < shared.MAX_TOOL_TURNS
    assert agent.MAX_FETCHES < shared.MAX_FETCHES


def test_the_checkpoint_is_passed_through_to_the_shared_loop(monkeypatch):
    """That hook is where "yield to the user" lives; a delegate that skipped it
    would compete with the very chat message it is answering."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('propose_task', json.dumps({'title': 'x'}))]),
        _msg(content='ok'),
    ])
    hits = []
    agent.run('add x', checkpoint=lambda: hits.append(1))
    assert len(hits) >= 2, 'expected a checkpoint before each model and tool call'
