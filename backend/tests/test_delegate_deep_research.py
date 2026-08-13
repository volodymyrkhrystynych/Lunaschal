"""The delegate's deep-research tool.

The nested gather pass and the synthesis call are both faked: what's under
test is the budget, the refusal/failure paths, and that the caller's
checkpoint actually reaches the nested loop.
"""
import time

from backend.delegate import deep_research
from backend.research import agent as shared


def _gather_result(**overrides):
    result = {
        'messages': [{'role': 'assistant', 'content': 'Found the answer.'}],
        'steps': [],
        'sources': [{'url': 'https://ex.com/a', 'title': 'A'}],
        'turns': 4,
        'truncated': False,
    }
    result.update(overrides)
    return result


def test_a_report_is_returned_with_its_sources(monkeypatch):
    monkeypatch.setattr(deep_research.agent, 'gather', lambda *a, **k: _gather_result())
    monkeypatch.setattr(deep_research, 'chat_messages', lambda messages: 'The answer is 42.')

    text, event = deep_research.run_tool('deep_research', {'query': 'what is the answer'})

    assert text == 'The answer is 42.'
    assert event == {
        'tool': 'deep_research', 'arg': 'what is the answer', 'ok': True,
        'count': 1, 'turns': 4, 'sources': [{'url': 'https://ex.com/a', 'title': 'A'}],
        'timedOut': False,
    }


def test_an_empty_query_is_refused_without_running_anything(monkeypatch):
    called = []
    monkeypatch.setattr(deep_research.agent, 'gather', lambda *a, **k: called.append(1))

    text, event = deep_research.run_tool('deep_research', {'query': '   '})

    assert not called, 'a blank question must not spend any budget'
    assert event['ok'] is False
    assert 'question is required' in event['error']


def test_run_tool_survives_non_dict_arguments():
    text, event = deep_research.run_tool('deep_research', None)
    assert event['ok'] is False


def test_a_blank_synthesis_is_reported_as_a_failure(monkeypatch):
    """An empty write-up would otherwise hand the delegate's own model a blank
    tool result it has to paper over, the same failure _summary() guards
    against one level up."""
    monkeypatch.setattr(deep_research.agent, 'gather', lambda *a, **k: _gather_result())
    monkeypatch.setattr(deep_research, 'chat_messages', lambda messages: '   ')

    text, event = deep_research.run_tool('deep_research', {'query': 'q'})

    assert event['ok'] is False
    assert 'no answer' in event['error']
    # The sources gathered before synthesis failed are still worth keeping.
    assert event['sources'] == [{'url': 'https://ex.com/a', 'title': 'A'}]


def test_the_checkpoint_reaches_the_nested_gather_call(monkeypatch):
    seen = {}

    def fake_gather(system, query, **kwargs):
        seen['checkpoint'] = kwargs.get('checkpoint')
        return _gather_result()

    monkeypatch.setattr(deep_research.agent, 'gather', fake_gather)
    monkeypatch.setattr(deep_research, 'chat_messages', lambda messages: 'ok')

    marker = object()
    deep_research.run_tool('deep_research', {'query': 'q'}, checkpoint=marker)
    assert seen['checkpoint'] is marker


def test_the_synthesis_turn_sees_the_gathered_transcript(monkeypatch):
    monkeypatch.setattr(deep_research.agent, 'gather', lambda *a, **k: _gather_result(
        messages=[
            {'role': 'system', 'content': 'sys'},
            {'role': 'user', 'content': 'q'},
            {'role': 'assistant', 'content': 'Found the answer.'},
        ],
    ))
    seen = {}

    def fake_chat_messages(messages):
        seen['messages'] = messages
        return 'written up'

    monkeypatch.setattr(deep_research, 'chat_messages', fake_chat_messages)
    deep_research.run_tool('deep_research', {'query': 'q'})

    assert seen['messages'][-1] == {'role': 'user', 'content': deep_research.ANSWER_INSTRUCTION}
    assert seen['messages'][2] == {'role': 'assistant', 'content': 'Found the answer.'}


def test_the_budget_is_deeper_than_the_background_research_pass():
    """"Deep" has to mean something: at minimum, more than the pass the Ideas
    agent already runs in the background."""
    assert deep_research.MAX_DEEP_TURNS > shared.MAX_TOOL_TURNS
    assert deep_research.MAX_DEEP_FETCHES > shared.MAX_FETCHES


def test_only_web_tools_are_offered_to_the_nested_pass(monkeypatch):
    """No propose_* tools and no wiki — this is a general web research pass,
    not a repeat of the Ideas agent's toolbox."""
    seen = {}

    def fake_gather(system, query, **kwargs):
        seen['tools'] = kwargs.get('tools')
        seen['dispatch'] = kwargs.get('dispatch')
        return _gather_result()

    monkeypatch.setattr(deep_research.agent, 'gather', fake_gather)
    monkeypatch.setattr(deep_research, 'chat_messages', lambda messages: 'ok')
    deep_research.run_tool('deep_research', {'query': 'q'})

    from backend.research import web
    assert seen['tools'] == web.TOOLS
    assert set(seen['dispatch']) == {'web_search', 'web_fetch'}


def test_tool_definition_requires_a_query():
    [tool] = deep_research.TOOLS
    assert tool['function']['name'] == 'deep_research'
    assert tool['function']['parameters']['required'] == ['query']


# --- Running out of time produces an answer, not an error -----------------


def test_a_timed_out_pass_is_salvaged_into_an_answer(monkeypatch):
    """The synthesis turn already exists — only the instruction changes. A pass
    that read six pages and ran out of time has an answer in it, and reporting
    a failure would throw away work already paid for."""
    seen = {}

    def fake_chat_messages(messages):
        seen['instruction'] = messages[-1]['content']
        return 'What I did find: the answer is 42. I never confirmed the question.'

    monkeypatch.setattr(deep_research.agent, 'gather',
                        lambda *a, **k: _gather_result(timed_out=True, truncated=True))
    monkeypatch.setattr(deep_research, 'chat_messages', fake_chat_messages)

    text, event = deep_research.run_tool('deep_research', {'query': 'q'})

    assert seen['instruction'] == deep_research.SALVAGE_INSTRUCTION
    assert '42' in text
    # ok stays True: the delegate's own model must not discard an answer it
    # was just handed because the pass behind it was cut short.
    assert event['ok'] is True
    assert event['timedOut'] is True
    assert event['sources'] == [{'url': 'https://ex.com/a', 'title': 'A'}]


def test_a_pass_that_finished_gets_the_ordinary_instruction(monkeypatch):
    seen = {}

    def fake_chat_messages(messages):
        seen['instruction'] = messages[-1]['content']
        return 'The answer is 42.'

    monkeypatch.setattr(deep_research.agent, 'gather', lambda *a, **k: _gather_result())
    monkeypatch.setattr(deep_research, 'chat_messages', fake_chat_messages)

    _, event = deep_research.run_tool('deep_research', {'query': 'q'})

    assert seen['instruction'] == deep_research.ANSWER_INSTRUCTION
    assert event['timedOut'] is False


def test_the_nested_pass_is_bounded_by_the_outer_deadline(monkeypatch):
    """The pass gets its own budget, but never one that outlives the reply it
    belongs to — otherwise the chat timeout is decorative."""
    seen = {}

    def fake_gather(system, query, **kwargs):
        seen['deadline'] = kwargs.get('deadline')
        return _gather_result()

    monkeypatch.setattr(deep_research.agent, 'gather', fake_gather)
    monkeypatch.setattr(deep_research, 'chat_messages', lambda messages: 'ok')

    outer = time.monotonic() + 5
    deep_research.run_tool('deep_research', {'query': 'q'}, deadline=outer)

    # Its own default is ten minutes; the five seconds left on the outer one wins.
    assert seen['deadline'] == outer


def test_timeouts_turned_off_leaves_the_pass_unbounded(client, monkeypatch):
    seen = {}

    def fake_gather(system, query, **kwargs):
        seen['deadline'] = kwargs.get('deadline')
        return _gather_result()

    client.patch('/api/settings/ai', json={'researchTimeoutEnabled': False})
    monkeypatch.setattr(deep_research.agent, 'gather', fake_gather)
    monkeypatch.setattr(deep_research, 'chat_messages', lambda messages: 'ok')
    deep_research.run_tool('deep_research', {'query': 'q'})

    assert seen['deadline'] is None
