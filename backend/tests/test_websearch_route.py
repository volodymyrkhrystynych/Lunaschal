"""Tests for POST /api/chat/websearch/stream — the SSE framing around
backend.ai.websearch_chat.stream_reply, mirroring how test_chat_journal_context.py
exercises /api/chat/stream."""
import json


def _capture_stream(monkeypatch, events):
    captured = {}

    def fake_stream_reply(messages):
        captured['messages'] = messages
        yield from events

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.websearch_chat.stream_reply', fake_stream_reply)
    return captured


def _post(client, body):
    return client.post(
        '/api/chat/websearch/stream', data=json.dumps(body), content_type='application/json'
    )


def _parse_sse(raw: bytes) -> list[dict | str]:
    events = []
    for line in raw.decode().splitlines():
        if not line.startswith('data: '):
            continue
        data = line[len('data: '):]
        events.append('[DONE]' if data == '[DONE]' else json.loads(data))
    return events


def test_requires_ai_configured(client, monkeypatch):
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: False)
    resp = client.post('/api/chat/websearch/stream', json={'messages': []})
    assert resp.status_code == 400


def test_step_content_and_done_events_are_framed_as_sse(client, monkeypatch):
    events = [
        ('step', {'tool': 'web_search', 'arg': 'fsrs', 'ok': True, 'count': 1}),
        ('content', 'The '),
        ('content', 'answer.'),
        ('done', {'steps': [{'tool': 'web_search'}], 'sources': [{'url': 'https://ex.com'}]}),
    ]
    captured = _capture_stream(monkeypatch, events)
    resp = _post(client, {'messages': [{'role': 'user', 'content': 'what is fsrs'}]})
    assert resp.status_code == 200

    parsed = _parse_sse(resp.get_data())
    assert parsed[0] == {'tool': 'web_search', 'arg': 'fsrs', 'ok': True, 'count': 1}
    assert parsed[1] == {'content': 'The '}
    assert parsed[2] == {'content': 'answer.'}
    assert parsed[3] == {
        'done': True,
        'steps': [{'tool': 'web_search'}],
        'sources': [{'url': 'https://ex.com'}],
    }
    assert parsed[-1] == '[DONE]'
    assert captured['messages'] == [{'role': 'user', 'content': 'what is fsrs'}]


def test_an_exception_becomes_an_error_event(client, monkeypatch):
    def fake_stream_reply(messages):
        raise RuntimeError('llama-server is down')
        yield  # pragma: no cover — makes this a generator

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.websearch_chat.stream_reply', fake_stream_reply)

    resp = _post(client, {'messages': []})
    parsed = _parse_sse(resp.get_data())
    assert parsed[0]['error'] == 'llama-server is down'
