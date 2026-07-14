"""Tests for POST /api/writing/conversations/<id>/summarize — the LLM helper is
monkeypatched at the routes-module boundary so no provider is ever called."""
from backend.routes import writing


def _seed_discussion(client, with_messages=True):
    pid = client.post(
        '/api/writing/projects', json={'title': 'My Story', 'description': 'A tale'}
    ).get_json()['id']
    did = client.post(f'/api/writing/projects/{pid}/conversations', json={}).get_json()['id']
    if with_messages:
        client.post(f'/api/chat/conversations/{did}/messages',
                    json={'role': 'user', 'content': 'What if the villain is her brother?'})
        client.post(f'/api/chat/conversations/{did}/messages',
                    json={'role': 'assistant', 'content': 'That raises the stakes nicely.'})
    return pid, did


def test_summarize_success_creates_note(client, monkeypatch):
    pid, did = _seed_discussion(client)
    captured = {}

    def fake_summarize(transcript, title, description=None):
        captured['transcript'] = transcript
        captured['title'] = title
        captured['description'] = description
        return {'title': 'Villain twist decision', 'content': '- Villain is her brother'}

    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(writing, 'summarize_discussion', fake_summarize)

    resp = client.post(f'/api/writing/conversations/{did}/summarize')
    assert resp.status_code == 201
    note = resp.get_json()
    assert note['title'] == 'Villain twist decision'
    assert note['content'] == '- Villain is her brother'
    assert note['docType'] == 'note'
    assert note['projectId'] == pid

    assert 'Author: What if the villain is her brother?' in captured['transcript']
    assert 'Assistant: That raises the stakes nicely.' in captured['transcript']
    assert captured['title'] == 'My Story'
    assert captured['description'] == 'A tale'

    notes = client.get(f'/api/writing/projects/{pid}/notes').get_json()
    assert [n['id'] for n in notes] == [note['id']]


def test_summarize_skips_system_messages(client, monkeypatch):
    pid, did = _seed_discussion(client)
    client.post(f'/api/chat/conversations/{did}/messages',
                json={'role': 'system', 'content': 'secret system prompt'})
    captured = {}

    def fake_summarize(transcript, title, description=None):
        captured['transcript'] = transcript
        return {'title': 'T', 'content': 'C'}

    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(writing, 'summarize_discussion', fake_summarize)

    assert client.post(f'/api/writing/conversations/{did}/summarize').status_code == 201
    assert 'secret system prompt' not in captured['transcript']


def test_summarize_ai_not_configured(client, monkeypatch):
    pid, did = _seed_discussion(client)
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: False)

    resp = client.post(f'/api/writing/conversations/{did}/summarize')
    assert resp.status_code == 400
    assert 'not configured' in resp.get_json()['error']


def test_summarize_empty_discussion(client, monkeypatch):
    pid, did = _seed_discussion(client, with_messages=False)
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)

    resp = client.post(f'/api/writing/conversations/{did}/summarize')
    assert resp.status_code == 400


def test_summarize_unknown_conversation(client, monkeypatch):
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)
    resp = client.post('/api/writing/conversations/nope/summarize')
    assert resp.status_code == 404


def test_summarize_rejects_non_writing_conversation(client, monkeypatch):
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)
    cid = client.post('/api/chat/conversations', json={}).get_json()['id']
    client.post(f'/api/chat/conversations/{cid}/messages', json={'role': 'user', 'content': 'hi'})

    resp = client.post(f'/api/writing/conversations/{cid}/summarize')
    assert resp.status_code == 404


def test_summarize_llm_failure_creates_nothing(client, monkeypatch):
    pid, did = _seed_discussion(client)
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(writing, 'summarize_discussion', lambda *a, **kw: None)

    resp = client.post(f'/api/writing/conversations/{did}/summarize')
    assert resp.status_code == 502
    assert client.get(f'/api/writing/projects/{pid}/notes').get_json() == []
