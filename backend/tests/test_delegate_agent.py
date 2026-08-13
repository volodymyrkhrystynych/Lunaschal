"""The research delegate's loop.

The model is a scripted fake throughout: what's under test is what the loop
hands back to the main chat model as a summary, and that it inherits the shared
loop's budget and truncation behaviour rather than re-implementing them.

The `propose_*` tools used to live here and no longer do — they are on the main
chat's own turn now (test_delegate_chat.py), because a delegate is handed one
`task` string and cannot see the conversation, so every detail the main model
did not restate was lost before a to-do was ever staged.
"""
import json
import time
from types import SimpleNamespace

import pytest

from backend.ai import priority
from backend.delegate import agent
from backend.research import agent as shared


@pytest.fixture(autouse=True)
def stub_web(monkeypatch):
    """The real web tools read settings out of the DB and then the internet;
    these tests are about the loop, not either."""
    def fake(name, args):
        query = args.get('query') or args.get('url') or ''
        return (f'results for {query}', {'tool': name, 'arg': query, 'ok': True, 'count': 1})

    monkeypatch.setattr(agent.web, 'run_tool', fake)


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


def test_the_summary_is_the_models_own_closing_message(monkeypatch):
    """Only the summary crosses back into the main conversation, never the
    transcript — that compression is the point of delegating."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'fsrs release date'}))]),
        _msg(content='FSRS 5 was released in July 2024.'),
    ])
    result = agent.run('when was FSRS 5 released')

    assert result['summary'] == 'FSRS 5 was released in July 2024.'
    assert 'messages' not in result


def test_steps_stream_before_the_result(monkeypatch):
    """The SSE route needs each step the moment its call finishes; with only a
    blocking form the events all arrive after the run, which is the silent
    spinner they exist to replace."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'FSRS'}))]),
        _msg(content='Done.'),
    ])
    kinds = [kind for kind, _ in agent.run_events('look up FSRS')]
    assert kinds == ['step', 'result']


def test_a_truncated_run_does_not_pass_off_a_half_sentence_as_its_summary(monkeypatch):
    """A turn cut off at the token ceiling arrives with no tool calls, exactly
    like a finished one. Handing that fragment to the main model as the summary
    is how the reply ends up describing work that never completed. What goes
    over instead is a salvage turn written from the whole transcript."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'FSRS'}))]),
        _msg(content='I read the page and then went on to'),
    ], finish_reasons=['tool_calls', 'length'])
    monkeypatch.setattr(agent, 'chat_messages',
                        lambda messages: 'FSRS is a spaced-repetition scheduler.')
    result = agent.run('look up FSRS')

    assert result['truncated'] is True
    assert 'went on to' not in result['summary']
    assert result['summary'] == 'FSRS is a spaced-repetition scheduler.'


def test_the_salvage_turn_reads_the_whole_transcript(monkeypatch):
    """The steps happened and the pages were read — all that is missing is the
    turn that would have written them up, and the material for it is sitting in
    the transcript."""
    seen = {}

    def fake_chat_messages(messages):
        seen['messages'] = messages
        return 'Here is what I found.'

    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'FSRS'}))]),
        _msg(content='cut off mid'),
    ], finish_reasons=['tool_calls', 'length'])
    monkeypatch.setattr(agent, 'chat_messages', fake_chat_messages)
    agent.run('look up FSRS')

    assert seen['messages'][-1] == {
        'role': 'user', 'content': agent.SALVAGE_INSTRUCTION,
    }
    assert any(m['role'] == 'tool' for m in seen['messages']), 'the tool results go too'


def test_a_failed_salvage_falls_back_to_the_honest_stand_in(monkeypatch):
    """The one thing the main model must never be handed is a blank, which it
    will paper over with something plausible."""
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'FSRS'}))]),
        _msg(content='cut off mid'),
    ], finish_reasons=['tool_calls', 'length'])

    def boom(messages):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(agent, 'chat_messages', boom)
    assert 'never summarised' in agent.run('look up FSRS')['summary']


def test_a_blank_salvage_falls_back_too(monkeypatch):
    _script(monkeypatch, [
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'FSRS'}))]),
        _msg(content='cut off mid'),
    ], finish_reasons=['tool_calls', 'length'])
    monkeypatch.setattr(agent, 'chat_messages', lambda messages: '   ')
    assert 'never summarised' in agent.run('look up FSRS')['summary']


def test_a_finished_run_never_pays_for_a_salvage_call(monkeypatch):
    """The common path is one function with no side effects; the model call is
    the recovery, not part of every run."""
    calls = []
    _script(monkeypatch, [_msg(content='FSRS is a scheduler.')])
    monkeypatch.setattr(agent, 'chat_messages', lambda messages: calls.append(1))

    result = agent.run('look up FSRS')
    assert calls == []
    assert result['summary'] == 'FSRS is a scheduler.'
    assert result['timedOut'] is False


def test_a_run_that_did_nothing_says_so(monkeypatch):
    """An empty summary is one the main model will paper over with a guess."""
    _script(monkeypatch, [_msg(content='')], finish_reasons=['stop'])
    assert agent.run('...')['summary'] == (
        'The delegate could not look anything up for that task.'
    )


def test_the_proposal_tools_are_no_longer_offered_here(monkeypatch):
    """They moved to the main chat's own turn. Left here they would be handed a
    task string with the conversation already paraphrased out of it — which is
    how "by Friday" stopped reaching the to-do it belonged on."""
    calls = _script(monkeypatch, [_msg(content='Nothing to do.')])
    agent.run('hello')

    offered = {t['function']['name'] for t in calls[0]['tools']}
    assert not any(name.startswith('propose_') for name in offered)
    assert 'ask_user' not in offered
    assert 'web_search' in offered


def test_the_delegate_knows_what_day_it_is(monkeypatch):
    """It never did. A task like "what's on this weekend" reached a model with
    no idea when now was, so any date it produced was invented."""
    from datetime import datetime

    calls = _script(monkeypatch, [_msg(content='Nothing to do.')])
    agent.run('hello')

    system = calls[0]['messages'][0]['content']
    assert datetime.now().strftime('%d %B %Y') in system


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
        _msg(tool_calls=[_call('web_search', json.dumps({'query': 'x'}))]),
        _msg(content='ok'),
    ])
    hits = []
    agent.run('look up x', checkpoint=lambda: hits.append(1))
    assert len(hits) >= 2, 'expected a checkpoint before each model and tool call'


def test_the_model_is_also_offered_deep_research(monkeypatch):
    calls = _script(monkeypatch, [_msg(content='Nothing to do.')])
    agent.run('hello')

    offered = {t['function']['name'] for t in calls[0]['tools']}
    assert 'deep_research' in offered


def test_deep_research_receives_the_delegates_own_checkpoint_and_deadline(monkeypatch):
    """A long deep_research call has to cooperate with the same yield-to-the-
    user gate as the rest of the loop, or it would compete with the very chat
    message it is answering. The module-level DISPATCH entry gets neither a
    checkpoint nor a deadline, so this only holds if run_events rebinds it per
    call — and the deadline matters for the same reason the checkpoint does: a
    nested pass that outlives the reply it belongs to is unbounded again."""
    seen = {}

    def fake_run_tool(name, args, checkpoint=None, deadline=None):
        seen['checkpoint'] = checkpoint
        seen['deadline'] = deadline
        return ('a thorough answer', {'tool': 'deep_research', 'arg': args.get('query'), 'ok': True})

    monkeypatch.setattr(agent.deep_research, 'run_tool', fake_run_tool)
    _script(monkeypatch, [
        _msg(tool_calls=[_call('deep_research', json.dumps({'query': 'x'}))]),
        _msg(content='Found it.'),
    ])

    checkpoint = lambda: None
    agent.run('research x thoroughly', checkpoint=checkpoint, deadline=time.monotonic() + 300)
    assert seen['checkpoint'] is checkpoint
    # Its own search budget, clamped by the 300s outer one it was handed.
    assert seen['deadline'] is not None
