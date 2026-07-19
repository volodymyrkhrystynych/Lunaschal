"""Post-review clarify chat: source selection, tool loop, transcript continuity."""
import asyncio
import json
from types import SimpleNamespace

from backend.ai import llm, mcp_client


class FakeSession:
    """Initialized MCP session exposing one search tool."""

    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(
            name='search', description='Search docs',
            inputSchema={'type': 'object', 'properties': {'q': {'type': 'string'}}},
        )])

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(text='Docs: closures capture scope.')])


def _tool_call(name, args):
    return SimpleNamespace(id='tc1', function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _scripted_llm(monkeypatch, replies):
    """chat_with_tools returns each reply in order; records the message lists."""
    seen = []

    def fake(messages, tools):
        seen.append(list(messages))
        return replies[len(seen) - 1]

    monkeypatch.setattr(llm, 'chat_with_tools', fake)
    return seen


def _make_card(client, folder_provider=False):
    """Create an active card; optionally bind an MCP provider to its folder."""
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    sid = None
    if folder_provider:
        sid = client.post('/api/learning/mcp-servers', json={
            'name': 's', 'transport': 'stdio', 'command': 'npx',
        }).json['id']
        client.patch(f'/api/learning/folders/{fid}', json={'evidenceProviderId': sid})
    cid = client.post('/api/learning/cards', json={
        'question': 'What is a closure?', 'answer': 'A function plus captured scope.',
        'folderId': fid,
    }).json['id']
    return cid, sid


def _fake_session(monkeypatch, session):
    monkeypatch.setattr(mcp_client, 'run_tool_session',
                        lambda server, worker: asyncio.run(worker(session)))


def test_chat_with_folder_provider_runs_tool_loop(client, monkeypatch):
    session = FakeSession()
    _scripted_llm(monkeypatch, [
        SimpleNamespace(content=None, tool_calls=[_tool_call('search', {'q': 'closure'})]),
        SimpleNamespace(content='Per the docs, a closure captures scope.', tool_calls=None),
    ])
    cid, _ = _make_card(client, folder_provider=True)
    _fake_session(monkeypatch, session)

    r = client.post(f'/api/learning/cards/{cid}/chat', json={'message': 'Give me an example'})
    assert r.status_code == 200
    assert r.json['usedMcp'] is True
    assert 'captures scope' in r.json['reply']
    assert session.calls == [('search', {'q': 'closure'})]
    roles = [m['role'] for m in r.json['transcript']]
    assert roles == ['system', 'user', 'assistant', 'tool', 'assistant']
    # Card context and tools note live in the system message.
    system = r.json['transcript'][0]['content']
    assert 'What is a closure?' in system
    assert 'tools' in system


def test_chat_without_provider_is_model_only(client, monkeypatch):
    def _no_session(*a, **k):
        raise AssertionError('must not connect without a provider')
    monkeypatch.setattr(mcp_client, 'run_tool_session', _no_session)
    monkeypatch.setattr(llm, 'chat_messages', lambda messages: 'Plain explanation.')

    cid, _ = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/chat',
                    json={'message': 'Explain it', 'userAnswer': 'a function'})
    assert r.status_code == 200
    assert r.json['reply'] == 'Plain explanation.'
    assert r.json['usedMcp'] is False
    system = r.json['transcript'][0]['content']
    assert 'The answer the user gave during review: a function' in system
    # No tools note when the chat runs model-only.
    assert 'Never invent citations' not in system


def test_chat_transcript_continues(client, monkeypatch):
    monkeypatch.setattr(llm, 'chat_messages', lambda messages: 'Second reply.')
    cid, _ = _make_card(client)
    prior = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'first'},
        {'role': 'assistant', 'content': 'First reply.'},
    ]
    r = client.post(f'/api/learning/cards/{cid}/chat',
                    json={'message': 'And another example?', 'transcript': prior})
    assert r.status_code == 200
    t = r.json['transcript']
    assert t[:3] == prior
    assert t[3] == {'role': 'user', 'content': 'And another example?'}
    assert t[4] == {'role': 'assistant', 'content': 'Second reply.'}


def test_chat_explicit_server_overrides_folder(client, monkeypatch):
    session = FakeSession()
    _scripted_llm(monkeypatch, [SimpleNamespace(content='From other source.', tool_calls=None)])
    cid, _ = _make_card(client)  # folder has no provider
    other = client.post('/api/learning/mcp-servers', json={
        'name': 'other', 'transport': 'http', 'url': 'http://x',
    }).json['id']

    used = {}

    def fake_run(server, worker):
        used['name'] = server['name']
        return asyncio.run(worker(session))
    monkeypatch.setattr(mcp_client, 'run_tool_session', fake_run)

    r = client.post(f'/api/learning/cards/{cid}/chat',
                    json={'message': 'hi', 'mcpServerId': other})
    assert r.status_code == 200
    assert r.json['usedMcp'] is True
    assert used['name'] == 'other'


def test_chat_explicit_null_forces_model_only(client, monkeypatch):
    def _no_session(*a, **k):
        raise AssertionError('mcpServerId=null must not connect')
    monkeypatch.setattr(mcp_client, 'run_tool_session', _no_session)
    monkeypatch.setattr(llm, 'chat_messages', lambda messages: 'ok')

    cid, _ = _make_card(client, folder_provider=True)
    r = client.post(f'/api/learning/cards/{cid}/chat',
                    json={'message': 'hi', 'mcpServerId': None})
    assert r.status_code == 200
    assert r.json['usedMcp'] is False


def test_chat_validation_errors(client):
    cid, _ = _make_card(client)
    assert client.post(f'/api/learning/cards/{cid}/chat', json={}).status_code == 400
    assert client.post(f'/api/learning/cards/{cid}/chat',
                       json={'message': 'hi', 'mcpServerId': 'nope'}).status_code == 400
    assert client.post('/api/learning/cards/missing/chat',
                       json={'message': 'hi'}).status_code == 404


def test_chat_provider_failure_is_502(client, monkeypatch):
    cid, _ = _make_card(client, folder_provider=True)

    def _boom(server, worker):
        raise RuntimeError('spawn failed')
    monkeypatch.setattr(mcp_client, 'run_tool_session', _boom)
    r = client.post(f'/api/learning/cards/{cid}/chat', json={'message': 'hi'})
    assert r.status_code == 502


def test_chat_tool_unsupported_falls_back_to_plain(client, monkeypatch):
    """Unlike verification, the study chat degrades to a model-only reply."""
    session = FakeSession()

    def raise_unsupported(messages, tools):
        raise llm.ToolCallingUnsupported('no tools')
    monkeypatch.setattr(llm, 'chat_with_tools', raise_unsupported)
    monkeypatch.setattr(llm, 'chat_messages', lambda messages: 'Fallback reply.')
    _fake_session(monkeypatch, session)

    cid, _ = _make_card(client, folder_provider=True)
    r = client.post(f'/api/learning/cards/{cid}/chat', json={'message': 'hi'})
    assert r.status_code == 200
    assert r.json['reply'] == 'Fallback reply.'
    assert r.json['usedMcp'] is False


def test_tool_budget_exhaustion_forces_plain_reply(client, monkeypatch):
    session = FakeSession()
    _scripted_llm(monkeypatch, [
        SimpleNamespace(content=None, tool_calls=[_tool_call('search', {'q': 'x'})]),
    ] * 20)
    monkeypatch.setattr(llm, 'chat_messages', lambda messages: 'Forced final answer.')
    _fake_session(monkeypatch, session)

    cid, _ = _make_card(client, folder_provider=True)
    r = client.post(f'/api/learning/cards/{cid}/chat', json={'message': 'hi'})
    assert r.status_code == 200
    assert r.json['reply'] == 'Forced final answer.'
    assert len(session.calls) == 8  # MAX_TOOL_TURNS
