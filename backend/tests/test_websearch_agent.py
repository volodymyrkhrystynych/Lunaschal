"""The web-search chat tool loop.

The model is a scripted fake throughout: what's under test is the loop's own
behaviour — budgets and error handling. Unlike the Ideas research agent this
was adapted from, there's no priority gate (no background job competes for
the LLM here) and the loop takes a full message list rather than a single
flattened question, since this is an ongoing multi-turn chat.
"""
from types import SimpleNamespace

from backend.websearch import agent


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


QUESTION = [{'role': 'user', 'content': 'question'}]


def test_a_plain_answer_ends_the_loop_immediately(monkeypatch):
    calls = _script(monkeypatch, [_msg(content='I already know this.')])
    result = agent.gather('sys', QUESTION)

    assert len(calls) == 1
    assert result['turns'] == 1
    assert result['truncated'] is False
    assert result['messages'][-1] == {'role': 'assistant', 'content': 'I already know this.'}


def test_the_full_history_rides_along_in_the_conversation(monkeypatch):
    """Multi-turn: prior chat turns must reach the model, not just the new
    question — unlike a one-shot discussion, this is an ongoing chat."""
    history = [
        {'role': 'user', 'content': 'earlier turn'},
        {'role': 'assistant', 'content': 'earlier reply'},
        {'role': 'user', 'content': 'question'},
    ]
    calls = _script(monkeypatch, [_msg(content='ok')])
    agent.gather('sys', history)
    sent = calls[0]['messages']
    assert sent[0] == {'role': 'system', 'content': 'sys'}
    assert sent[1:] == history


def test_a_tool_call_is_executed_and_fed_back(client, monkeypatch):
    monkeypatch.setattr(agent.web, 'run_tool', lambda n, a: ('no results', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', '{"query":"fsrs"}')]),
        _msg(content='Nothing found.'),
    ])
    result = agent.gather('sys', QUESTION)

    tool_messages = [m for m in result['messages'] if m['role'] == 'tool']
    assert len(tool_messages) == 1
    assert tool_messages[0]['tool_call_id'] == 'c1'
    assert tool_messages[0]['content'] == 'no results'
    assert result['steps'] == [{'tool': 'web_search', 'ok': True}]
    assert result['truncated'] is False


def test_steps_are_emitted_as_they_happen(client, monkeypatch):
    """The UI shows "searching…" while it happens, not after."""
    monkeypatch.setattr(agent.web, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search'), _call('web_fetch', '{"url":"https://ex.com"}', 'c2')]),
        _msg(content='done'),
    ])
    seen = []
    agent.gather('sys', QUESTION, on_step=seen.append)
    assert [s['tool'] for s in seen] == ['web_search', 'web_fetch']


def test_the_turn_budget_is_enforced_and_reported(client, monkeypatch):
    monkeypatch.setattr(agent.web, 'run_tool', lambda n, a: ('x', {'tool': n, 'ok': True}))
    calls = _script(monkeypatch, [_msg(tool_calls=[_call('web_search')])])  # never stops

    result = agent.gather('sys', QUESTION, max_turns=3)
    assert len(calls) == 3
    assert result['turns'] == 3
    assert result['truncated'] is True, 'the caller can say the search was cut short'


def test_tool_turns_are_capped_in_length(client, monkeypatch):
    calls = _script(monkeypatch, [_msg(content='done')])
    agent.gather('sys', QUESTION)
    assert calls[0]['max_tokens'] == agent.TURN_MAX_TOKENS
    assert agent.TURN_MAX_TOKENS <= 1024


def test_malformed_tool_arguments_do_not_crash_the_loop(client, monkeypatch):
    seen_args = []

    def run_tool(name, args):
        seen_args.append(args)
        return ('ok', {'tool': name, 'ok': True})

    monkeypatch.setattr(agent.web, 'run_tool', run_tool)
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', 'not json at all')]),
        _msg(content='done'),
    ])
    result = agent.gather('sys', QUESTION)
    assert seen_args == [{}]
    assert result['truncated'] is False


def test_an_unknown_tool_is_reported_to_the_model(client, monkeypatch):
    _script(monkeypatch, [
        _msg(tool_calls=[_call('rm_rf')]),
        _msg(content='ok then'),
    ])
    result = agent.gather('sys', QUESTION)
    tool_message = [m for m in result['messages'] if m['role'] == 'tool'][0]
    assert 'Unknown tool' in tool_message['content']


def test_a_model_error_ends_the_loop_without_raising(monkeypatch):
    def boom(messages, tools, max_tokens=None):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(agent, 'chat_with_tools', boom)
    result = agent.gather('sys', QUESTION)
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
    result = agent.gather('sys', QUESTION, max_turns=5, max_fetches=2)
    assert result['turns'] == 5
    assert len(result['sources']) == 2, 'stops recording sources once the budget is spent'
    last_step = result['steps'][-1]
    assert last_step['ok'] is False
    assert 'budget' in last_step['error']


def test_sources_are_only_recorded_for_successful_fetches(client, monkeypatch):
    monkeypatch.setattr(agent.web, 'run_tool', lambda n, a: (
        'failed', {'tool': 'web_fetch', 'ok': False, 'error': 'refused'},
    ))
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_fetch', '{"url":"https://ex.com/a"}')]),
        _msg(content='done'),
    ])
    result = agent.gather('sys', QUESTION)
    assert result['sources'] == []


def test_only_web_tools_are_registered():
    names = {t['function']['name'] for t in agent.ALL_TOOLS}
    assert names == {'web_search', 'web_fetch'}
