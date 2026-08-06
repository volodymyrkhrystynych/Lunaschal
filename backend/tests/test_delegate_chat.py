"""The Chat tab's decide-delegate-answer turn, and its SSE framing.

The model is faked at two seams: `_delegate_call` (the decision turn) and
`chat_stream_events` (the answer). What's under test is the glue between them —
what crosses from the delegate into the answering prompt, and what reaches the
browser.
"""
import json
from types import SimpleNamespace

import pytest

from backend.delegate import chat as delegate_chat


def _call(task='do the thing', call_id='c1'):
    return SimpleNamespace(
        id=call_id,
        content=None,
        function=SimpleNamespace(
            name='delegate', arguments=json.dumps({'task': task})
        ),
    )


@pytest.fixture
def answered(monkeypatch):
    """Stub the answering turn and hand back what prompt it was given."""
    seen = {}

    def fake_stream_events(messages):
        seen['messages'] = messages
        yield ('content', 'Sure thing.')

    monkeypatch.setattr(delegate_chat, 'chat_stream_events', fake_stream_events)
    return seen


def _no_delegate(monkeypatch):
    monkeypatch.setattr(delegate_chat, '_delegate_call', lambda messages: None)


def _delegates(monkeypatch, result, task='do the thing'):
    monkeypatch.setattr(delegate_chat, '_delegate_call', lambda messages: _call(task))
    monkeypatch.setattr(
        delegate_chat.agent, 'run_events',
        lambda t, **kw: iter([('step', {'tool': 'propose_task', 'ok': True}),
                              ('result', result)]),
    )


def _drain(messages=None):
    return list(delegate_chat.stream_reply(messages or [{'role': 'user', 'content': 'hi'}]))


def test_an_ordinary_message_skips_the_delegate_entirely(monkeypatch, answered):
    _no_delegate(monkeypatch)
    events = _drain()

    assert [k for k, _ in events] == ['content', 'done']
    assert events[-1][1] == {'steps': [], 'sources': [], 'proposals': []}


def test_only_the_summary_crosses_into_the_answering_prompt(monkeypatch, answered):
    """The compression *is* the point of delegating: the main chat's context
    must not grow by the delegate's whole transcript."""
    _delegates(monkeypatch, {
        'steps': [{'tool': 'propose_task', 'ok': True}],
        'sources': [],
        'proposals': [{'kind': 'task', 'data': {'title': 'Call the dentist'}}],
        'summary': 'Staged a to-do: Call the dentist.',
        'truncated': False,
    })
    _drain()

    tool_messages = [m for m in answered['messages'] if m['role'] == 'tool']
    assert len(tool_messages) == 1
    assert tool_messages[0]['content'] == 'Staged a to-do: Call the dentist.'


def test_the_transcript_keeps_a_well_formed_tool_exchange(monkeypatch, answered):
    """A tool result with no preceding assistant tool_call is a malformed
    history that llama-server's template renders wrong."""
    _delegates(monkeypatch, {'steps': [], 'sources': [], 'proposals': [],
                             'summary': 'done', 'truncated': False})
    _drain()

    roles = [m['role'] for m in answered['messages']]
    assert roles.index('assistant') < roles.index('tool')
    assistant = next(m for m in answered['messages'] if m['role'] == 'assistant')
    assert assistant['tool_calls'][0]['function']['name'] == 'delegate'


def test_proposals_and_steps_ride_the_done_event(monkeypatch, answered):
    """The browser persists these onto the message metadata and turns proposals
    into confirm cards — a reload has to redraw the same trace, and the live
    events are gone by then."""
    proposals = [{'kind': 'calorie', 'data': {'description': 'burger', 'calories': 650}}]
    _delegates(monkeypatch, {
        'steps': [{'tool': 'propose_calorie_log', 'ok': True}],
        'sources': [{'url': 'https://example.com', 'title': 'Example'}],
        'proposals': proposals,
        'summary': 'Staged it.',
        'truncated': False,
    })
    events = _drain()

    kind, payload = events[-1]
    assert kind == 'done'
    assert payload['proposals'] == proposals
    assert payload['sources'] == [{'url': 'https://example.com', 'title': 'Example'}]
    # The step streamed live *and* rides the done event.
    assert ('step', {'tool': 'propose_task', 'ok': True}) in events
    assert len(payload['steps']) == 1


def test_a_failed_decision_turn_still_produces_a_reply(monkeypatch, answered):
    """The classifier's sin was swallowing failure into a fake result with no
    reply and no error. Losing the delegate must cost the delegate only."""
    def boom(messages, tools, max_tokens=None):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', boom)
    events = _drain()

    assert [k for k, _ in events] == ['content', 'done']


def test_the_decision_turn_is_capped(monkeypatch, answered):
    """Uncapped, a model that decided *not* to delegate writes a whole reply
    we are about to throw away — dead air before the real answer starts."""
    seen = {}

    def fake_turn(messages, tools, max_tokens=None):
        seen['max_tokens'] = max_tokens
        seen['tools'] = tools
        return SimpleNamespace(content='', tool_calls=None), 'stop'

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', fake_turn)
    _drain()

    assert seen['max_tokens'] == delegate_chat.DECISION_MAX_TOKENS
    assert [t['function']['name'] for t in seen['tools']] == ['delegate']


def test_thinking_and_content_stay_separate_channels(monkeypatch):
    """Reasoning rendered into the reply is the failure the split exists to
    prevent."""
    _no_delegate(monkeypatch)
    monkeypatch.setattr(
        delegate_chat, 'chat_stream_events',
        lambda messages: iter([('thinking', 'hmm'), ('content', 'Hello.')]),
    )
    events = _drain()
    assert [k for k, _ in events] == ['thinking', 'content', 'done']


def test_delegate_false_skips_the_decision_turn(monkeypatch, answered):
    """The voice listener and nudges speak their replies aloud, where a staged
    card is invisible and the decision turn is pure added latency."""
    def boom(*args, **kwargs):
        raise AssertionError('the decision turn must not run')

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', boom)
    events = list(delegate_chat.stream_reply(
        [{'role': 'user', 'content': 'hi'}], 'CUSTOM PROMPT', delegate=False
    ))
    assert [k for k, _ in events] == ['content', 'done']


# --- SSE framing on the route ---

def _post(client, body):
    resp = client.post('/api/chat/stream', data=json.dumps(body),
                       content_type='application/json')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _events(payload: str) -> list[dict]:
    return [json.loads(line[6:]) for line in payload.splitlines()
            if line.startswith('data: ') and line[6:] != '[DONE]']


def test_the_route_frames_every_event_kind(client, monkeypatch):
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.routes.chat.delegate_chat.stream_reply',
        lambda messages, system_prompt, delegate=True: iter([
            ('step', {'tool': 'web_search', 'ok': True, 'count': 3}),
            ('thinking', 'weighing it up'),
            ('content', 'Here you go.'),
            ('done', {'steps': [], 'sources': [], 'proposals': []}),
        ]),
    )
    body = _post(client, {'messages': [{'role': 'user', 'content': 'hi'}]})

    events = _events(body)
    # A step event goes out bare, keyed by `tool` — the shape the browser
    # already keys on, and what old websearch-mode messages persisted.
    assert events[0]['tool'] == 'web_search'
    assert events[1] == {'thinking': 'weighing it up'}
    assert events[2] == {'content': 'Here you go.'}
    assert events[3]['done'] is True
    assert body.rstrip().endswith('data: [DONE]')


def test_a_mid_stream_failure_reaches_the_browser(client, monkeypatch):
    """The bug this whole change came from was a request that produced no
    reply, no error and no log line."""
    def exploding(messages, system_prompt, delegate=True):
        yield ('content', 'partial')
        raise RuntimeError('llama-server died')

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', exploding)

    events = _events(_post(client, {'messages': []}))
    assert events[-1]['error'] == 'llama-server died'


def test_the_priority_mark_is_released_after_the_turn(client, monkeypatch):
    """Held across the delegate sub-loop *and* the streamed answer, then
    released — a leaked mark defers background work for a whole MARK_TTL."""
    from backend.ai import priority

    priority.reset()
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.routes.chat.delegate_chat.stream_reply',
        lambda messages, system_prompt, delegate=True: iter([('content', 'hi')]),
    )
    _post(client, {'messages': []})
    assert priority.active() is False


def test_a_caller_supplied_prompt_turns_the_delegate_off(client, monkeypatch):
    seen = {}

    def fake(messages, system_prompt, delegate=True):
        seen['delegate'] = delegate
        yield ('content', 'ok')

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', fake)

    _post(client, {'messages': [], 'systemPrompt': 'You are a nudge.'})
    assert seen['delegate'] is False

    _post(client, {'messages': []})
    assert seen['delegate'] is True
