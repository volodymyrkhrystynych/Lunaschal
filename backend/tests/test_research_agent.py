"""The research tool loop.

The model is a scripted fake throughout: what's under test is the loop's own
behaviour — budgets, error handling, and the checkpoint that makes background
work yield to the user.
"""
import threading
from types import SimpleNamespace

import pytest

from backend.ai import priority
from backend.research import agent


def _call(name, arguments='{}', call_id='c1'):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _script(monkeypatch, responses):
    """Feed the loop a fixed sequence of assistant messages."""
    calls = []

    def fake(messages, tools, max_tokens=None):
        calls.append({'messages': list(messages), 'max_tokens': max_tokens})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(agent, 'chat_with_tools', fake)
    return calls


@pytest.fixture(autouse=True)
def clean_gate():
    priority.reset()
    yield
    priority.reset()


def test_a_plain_answer_ends_the_loop_immediately(monkeypatch):
    calls = _script(monkeypatch, [_msg(content='I already know this.')])
    result = agent.gather('sys', 'question')

    assert len(calls) == 1
    assert result['turns'] == 1
    assert result['truncated'] is False
    assert result['messages'][-1] == {'role': 'assistant', 'content': 'I already know this.'}


def test_a_tool_call_is_executed_and_fed_back(client, monkeypatch):
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('wiki is empty', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list')]),
        _msg(content='Nothing in the wiki yet.'),
    ])
    result = agent.gather('sys', 'question')

    tool_messages = [m for m in result['messages'] if m['role'] == 'tool']
    assert len(tool_messages) == 1
    assert tool_messages[0]['tool_call_id'] == 'c1'
    assert tool_messages[0]['content'] == 'wiki is empty'
    assert result['steps'] == [{'tool': 'wiki_list', 'ok': True}]
    assert result['truncated'] is False


def test_steps_are_emitted_as_they_happen(client, monkeypatch):
    """The UI shows "searching…" while it happens, not after."""
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list'), _call('wiki_search', '{"query":"fsrs"}', 'c2')]),
        _msg(content='done'),
    ])
    seen = []
    agent.gather('sys', 'q', on_step=seen.append)
    assert [s['tool'] for s in seen] == ['wiki_list', 'wiki_search']


def test_the_turn_budget_is_enforced_and_reported(client, monkeypatch):
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    calls = _script(monkeypatch, [_msg(tool_calls=[_call('wiki_list')])])  # never stops

    result = agent.gather('sys', 'q', max_turns=3)
    assert len(calls) == 3
    assert result['turns'] == 3
    assert result['truncated'] is True, 'the caller can say the search was cut short'


def test_tool_turns_are_capped_in_length(client, monkeypatch):
    """Nothing preempts a generation once it starts, so turn length is the
    granularity at which this loop can yield to a chat message."""
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    calls = _script(monkeypatch, [_msg(content='done')])
    agent.gather('sys', 'q')
    assert calls[0]['max_tokens'] == agent.TURN_MAX_TOKENS
    assert agent.TURN_MAX_TOKENS <= 1024


def test_malformed_tool_arguments_do_not_crash_the_loop(client, monkeypatch):
    seen_args = []

    def run_tool(name, args):
        seen_args.append(args)
        return ('ok', {'tool': name, 'ok': True})

    monkeypatch.setattr(agent.wiki, 'run_tool', run_tool)
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_search', 'not json at all')]),
        _msg(content='done'),
    ])
    result = agent.gather('sys', 'q')
    assert seen_args == [{}]
    assert result['truncated'] is False


def test_an_unknown_tool_is_reported_to_the_model(client, monkeypatch):
    _script(monkeypatch, [
        _msg(tool_calls=[_call('rm_rf')]),
        _msg(content='ok then'),
    ])
    result = agent.gather('sys', 'q')
    tool_message = [m for m in result['messages'] if m['role'] == 'tool'][0]
    assert 'Unknown tool' in tool_message['content']


def test_a_model_error_ends_the_loop_without_raising(monkeypatch):
    def boom(messages, tools, max_tokens=None):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(agent, 'chat_with_tools', boom)
    result = agent.gather('sys', 'q')
    assert result['steps'][0]['ok'] is False
    assert 'llama-server is down' in result['steps'][0]['error']


def test_fetch_budget_is_enforced(client, monkeypatch):
    monkeypatch.setattr(
        agent.web, 'run_tool',
        lambda n, a: ('page text', {'tool': 'web_fetch', 'ok': True,
                                    'url': a.get('url'), 'title': 'T'}),
    )
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_fetch', '{"url":"https://ex.com/a"}')]),
    ])
    result = agent.gather('sys', 'q', max_turns=5, max_fetches=2)

    fetched = [s for s in result['steps'] if s.get('ok')]
    refused = [s for s in result['steps'] if s.get('error') == 'fetch budget exhausted']
    assert len(fetched) == 2
    assert refused, 'the model is told the budget ran out rather than silently looping'


def test_sources_come_from_what_was_actually_fetched(client, monkeypatch):
    """Not from what the model later claims it read."""
    monkeypatch.setattr(
        agent.web, 'run_tool',
        lambda n, a: ('text', {'tool': 'web_fetch', 'ok': True,
                               'url': 'https://ex.com/final', 'title': 'Final'}),
    )
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_fetch', '{"url":"https://ex.com/start"}')]),
        _msg(content='I read one page.'),
    ])
    result = agent.gather('sys', 'q')
    # The post-redirect URL, because that is the page whose text was used.
    assert result['sources'] == [{'url': 'https://ex.com/final', 'title': 'Final'}]


def test_a_failed_fetch_is_not_recorded_as_a_source(client, monkeypatch):
    monkeypatch.setattr(
        agent.web, 'run_tool',
        lambda n, a: ('Refused', {'tool': 'web_fetch', 'ok': False, 'error': 'unsafe'}),
    )
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_fetch', '{"url":"http://127.0.0.1/"}')]),
        _msg(content='could not read it'),
    ])
    assert agent.gather('sys', 'q')['sources'] == []


# --- The checkpoint: yielding and cancelling ---

def test_checkpoint_runs_before_every_model_and_tool_call(client, monkeypatch):
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list'), _call('wiki_search', '{}', 'c2')]),
        _msg(content='done'),
    ])
    hits = []
    agent.gather('sys', 'q', checkpoint=lambda: hits.append(1))
    # 2 model turns + 2 tool calls
    assert len(hits) == 4


def test_cancelling_stops_the_loop(client, monkeypatch):
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    _script(monkeypatch, [_msg(tool_calls=[_call('wiki_list')])])

    cancel = threading.Event()
    cancel.set()
    checkpoint = agent.make_checkpoint(cancel=cancel, gate=False)

    with pytest.raises(agent.Cancelled):
        agent.gather('sys', 'q', checkpoint=checkpoint)


def test_the_standard_checkpoint_waits_for_the_user_to_finish():
    """The whole point of the gate: background turns park while a human waits."""
    token = priority.begin('chat.stream')
    released = threading.Event()

    def finish():
        priority.end(token)
        released.set()

    threading.Timer(0.05, finish).start()
    agent.make_checkpoint(gate=True)()
    assert released.is_set()


def test_cancellation_is_checked_after_waiting():
    """A run cancelled while parked stops at the next step rather than firing
    one more model call."""
    priority.begin('chat.stream')  # never released, so wait times out
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()

    with pytest.raises(agent.Cancelled):
        agent.make_checkpoint(cancel=cancel, gate=True)()


def test_gather_events_yields_each_step_before_the_result(client, monkeypatch):
    """The SSE endpoint depends on this: with the blocking form, every tool
    event only lands after gathering ends, which is the silent spinner the
    events exist to replace."""
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list')]),
        _msg(tool_calls=[_call('wiki_search', '{"query":"q"}', 'c2')]),
        _msg(content='done'),
    ])

    kinds = []
    result = None
    for kind, payload in agent.gather_events('sys', 'q'):
        kinds.append(kind)
        if kind == 'result':
            result = payload

    assert kinds == ['step', 'step', 'result'], 'steps stream, result comes last'
    assert result['turns'] == 3
    assert [s['tool'] for s in result['steps']] == ['wiki_list', 'wiki_search']


def test_gather_is_the_blocking_wrapper_over_the_same_loop(client, monkeypatch):
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list')]),
        _msg(content='done'),
    ])
    result = agent.gather('sys', 'q')
    assert result['steps'] == [{'tool': 'wiki_list', 'ok': True}]
    assert result['truncated'] is False


def test_tool_definitions_cover_web_and_wiki():
    names = {t['function']['name'] for t in agent.ALL_TOOLS}
    assert names == {
        'web_search', 'web_fetch', 'wiki_list', 'wiki_search', 'wiki_read'
    }
    assert set(agent._DISPATCH) == names
