"""The research tool loop.

The model is a scripted fake throughout: what's under test is the loop's own
behaviour — budgets, error handling, and the checkpoint that makes background
work yield to the user.
"""
import threading
import time
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


def _script(monkeypatch, responses, finish_reasons=None):
    """Feed the loop a fixed sequence of assistant messages.

    `finish_reasons` runs alongside, defaulting to a clean stop — pass 'length'
    to simulate a turn cut off at the token ceiling.
    """
    calls = []

    def fake(messages, tools, max_tokens=None):
        calls.append({'messages': list(messages), 'max_tokens': max_tokens})
        i = min(len(calls) - 1, len(responses) - 1)
        reason = (finish_reasons or [])[i] if finish_reasons and i < len(finish_reasons) else None
        if reason is None:
            reason = 'tool_calls' if responses[i].tool_calls else 'stop'
        return responses[i], reason

    monkeypatch.setattr(agent, 'chat_tool_turn', fake)
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


def test_a_turn_cut_off_at_the_token_ceiling_is_not_a_finished_run(monkeypatch):
    """A truncated turn arrives with no tool calls, exactly like a finished one.

    Live, this is what a 768-token ceiling does when the model starts writing a
    summary instead of calling a tool — and the run reported itself complete.
    """
    _script(monkeypatch,
            [_msg(content='The research is complete. Below is a summ')],
            finish_reasons=['length'])
    result = agent.gather('sys', 'q')

    assert result['truncated'] is True, 'a cut-off turn must not read as "done gathering"'


def test_a_clean_stop_is_a_finished_run(monkeypatch):
    _script(monkeypatch, [_msg(content='Done.')], finish_reasons=['stop'])
    assert agent.gather('sys', 'q')['truncated'] is False


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

    monkeypatch.setattr(agent, 'chat_tool_turn', boom)
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


def test_a_tool_that_reports_its_own_sources_list_contributes_them(client, monkeypatch):
    """A tool that wraps a nested pass of its own (the delegate's deep_research)
    can't report a single url/title pair like web_fetch does — it reports a
    `sources` list on its event instead, and the outer loop has to fold those
    in too or the UI's Sources section would miss most of what was read."""
    monkeypatch.setattr(
        agent.wiki, 'run_tool',
        lambda n, a: ('report', {'tool': 'deep_research', 'ok': True, 'sources': [
            {'url': 'https://ex.com/a', 'title': 'A'},
            {'url': 'https://ex.com/b', 'title': 'B'},
        ]}),
    )
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list')]),
        _msg(content='done'),
    ])
    result = agent.gather('sys', 'q')
    assert result['sources'] == [
        {'url': 'https://ex.com/a', 'title': 'A'},
        {'url': 'https://ex.com/b', 'title': 'B'},
    ]


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


# --- Wall-clock deadlines -------------------------------------------------
#
# Every budget above this point is a count: turns, fetches, tokens. A run that
# took an hour was inside all of them, which is what these bound instead.


def test_a_run_with_no_deadline_is_unbounded_as_before(monkeypatch):
    _script(monkeypatch, [_msg(content='done')])
    result = agent.gather('sys', 'q')
    assert result['timed_out'] is False


def test_the_loop_stops_before_a_turn_it_has_no_time_for(monkeypatch):
    """The deadline is checked where `checkpoint` is, and for the same reason:
    a turn is the granularity at which this loop can stop."""
    calls = _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list')]),
        _msg(content='done'),
    ])
    monkeypatch.setattr(agent.wiki, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))

    # Already past it: not one model call should be made.
    result = agent.gather('sys', 'q', deadline=time.monotonic() - 1)

    assert calls == [], 'a deadline already spent buys nothing by starting'
    assert result['timed_out'] is True
    assert result['turns'] == 0
    # Out of time is a stricter case of truncated, never a clean finish.
    assert result['truncated'] is True


def test_running_out_mid_turn_still_answers_every_tool_call(monkeypatch):
    """The transcript is what the caller's salvage turn is handed, and an
    assistant turn whose tool_calls have no replies is a malformed exchange —
    so a call that never runs still gets a tool message saying why."""
    # First check is the top of the turn (still in time, so the model runs);
    # the next is inside the tool loop, by which point the clock has passed it.
    clock = iter([0, 999, 999, 999])
    monkeypatch.setattr(agent.time, 'monotonic', lambda: next(clock))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('wiki_list', call_id='c1')]),
        _msg(content='never reached'),
    ])
    ran = []
    monkeypatch.setattr(agent.wiki, 'run_tool',
                        lambda n, a: (ran.append(n), ('x', {'tool': n, 'ok': True}))[1])

    result = agent.gather('sys', 'q', deadline=100)

    assert ran == [], 'the tool itself must not run once time is up'
    assert result['timed_out'] is True
    tool_messages = [m for m in result['messages'] if m['role'] == 'tool']
    assert len(tool_messages) == 1
    assert 'Out of time' in tool_messages[0]['content']
    assert tool_messages[0]['tool_call_id'] == 'c1'
    assert result['steps'] == [
        {'tool': 'wiki_list', 'arg': None, 'ok': False, 'error': 'out of time'}
    ]


def test_deadline_from_clamps_an_inner_budget_to_the_outer_one():
    """The one place "inner never outlives outer" is written down — clamping
    at each call site is how one of them ends up not clamping."""
    now = time.monotonic()
    outer = now + 10

    assert agent.deadline_from(600, outer) == pytest.approx(outer)
    assert agent.deadline_from(5, outer) == pytest.approx(now + 5, abs=0.5)
    # No inner budget of its own must not remove the one wrapping it.
    assert agent.deadline_from(0, outer) == outer
    assert agent.deadline_from(None, outer) == outer
    assert agent.deadline_from(None, None) is None
    assert agent.deadline_from(30) == pytest.approx(now + 30, abs=0.5)
