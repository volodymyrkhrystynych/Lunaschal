"""MCP registry CRUD, tools mapping, and the verification agent loop."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.ai import llm, mcp_client


# ---------------------------------------------------------------- pure helpers

def test_mcp_tools_to_openai():
    tools = [SimpleNamespace(
        name='search_docs',
        description='Search the docs',
        inputSchema={'type': 'object', 'properties': {'query': {'type': 'string'}}},
    ), SimpleNamespace(name='bare', description=None, inputSchema=None)]
    mapped = mcp_client.mcp_tools_to_openai(tools)
    assert mapped[0] == {
        'type': 'function',
        'function': {
            'name': 'search_docs',
            'description': 'Search the docs',
            'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}},
        },
    }
    assert mapped[1]['function']['description'] == ''
    assert mapped[1]['function']['parameters'] == {'type': 'object', 'properties': {}}


def test_tool_result_text():
    result = SimpleNamespace(content=[
        SimpleNamespace(text='first'), SimpleNamespace(text=None), SimpleNamespace(text='second'),
    ])
    assert mcp_client.tool_result_text(result) == 'first\nsecond'
    assert mcp_client.tool_result_text(SimpleNamespace(content=[])) == '(no text content)'


# ---------------------------------------------------------------- CRUD

def test_mcp_server_crud(client):
    r = client.post('/api/learning/mcp-servers', json={
        'name': 'context7', 'transport': 'stdio', 'command': 'npx',
        'args': ['-y', '@upstash/context7-mcp'], 'env': {'KEY': 'v'},
    })
    assert r.status_code == 201
    sid = r.json['id']

    assert client.post('/api/learning/mcp-servers', json={
        'name': 'context7', 'transport': 'stdio', 'command': 'npx',
    }).status_code == 400  # duplicate name
    assert client.post('/api/learning/mcp-servers', json={
        'name': 'x', 'transport': 'stdio',
    }).status_code == 400  # stdio without command
    assert client.post('/api/learning/mcp-servers', json={
        'name': 'x', 'transport': 'http',
    }).status_code == 400  # http without url

    servers = client.get('/api/learning/mcp-servers').json
    assert len(servers) == 1
    assert servers[0]['args'] == ['-y', '@upstash/context7-mcp']
    assert servers[0]['env'] == {'KEY': 'v'}

    assert client.patch(f'/api/learning/mcp-servers/{sid}',
                        json={'name': 'ctx7'}).status_code == 200
    assert client.get('/api/learning/mcp-servers').json[0]['name'] == 'ctx7'

    # Deleting a bound server unbinds its folders (FK SET NULL).
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    client.patch(f'/api/learning/folders/{fid}', json={'evidenceProviderId': sid})
    assert client.delete(f'/api/learning/mcp-servers/{sid}').status_code == 200
    assert client.get('/api/learning/folders').json[0]['evidenceProviderId'] is None


# ---------------------------------------------------------------- agent loop

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
        return SimpleNamespace(content=[SimpleNamespace(text='Docs say the answer is 8.')])


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


def _setup_verifiable_card(client, monkeypatch, session):
    sid = client.post('/api/learning/mcp-servers', json={
        'name': 's', 'transport': 'stdio', 'command': 'npx',
    }).json['id']
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    client.patch(f'/api/learning/folders/{fid}', json={'evidenceProviderId': sid})
    cid = client.post('/api/learning/cards', json={
        'question': 'What is 2^3?', 'answer': 'The answer is 4.', 'folderId': fid,
    }).json['id']
    monkeypatch.setattr(mcp_client, 'run_tool_session',
                        lambda server, worker: asyncio.run(worker(session)))
    return cid


def test_verify_builds_cited_case(client, monkeypatch):
    session = FakeSession()
    case_json = json.dumps({
        'verdict': 'contradicts',
        'summary': 'The docs give 8, not 4.',
        'proposedAnswer': 'The answer is 8.',
        'citations': [{'title': 'Docs', 'source': 'search', 'quote': 'the answer is 8'}],
    })
    _scripted_llm(monkeypatch, [
        SimpleNamespace(content=None, tool_calls=[_tool_call('search', {'q': '2^3'})]),
        SimpleNamespace(content=case_json, tool_calls=None),
    ])
    cid = _setup_verifiable_card(client, monkeypatch, session)

    r = client.post(f'/api/learning/cards/{cid}/verify', json={})
    assert r.status_code == 200
    assert r.json['status'] == 'ok'
    assert r.json['case']['verdict'] == 'contradicts'
    assert r.json['case']['citations'][0]['quote'] == 'the answer is 8'
    assert session.calls == [('search', {'q': '2^3'})]
    # Transcript carries the tool exchange for stateless follow-ups.
    roles = [m['role'] for m in r.json['transcript']]
    assert roles == ['system', 'user', 'assistant', 'tool', 'assistant']


def test_verify_not_found_verdict(client, monkeypatch):
    session = FakeSession()
    _scripted_llm(monkeypatch, [SimpleNamespace(
        content=json.dumps({'verdict': 'notFound', 'summary': 'Nothing.', 'citations': []}),
        tool_calls=None,
    )])
    cid = _setup_verifiable_card(client, monkeypatch, session)
    r = client.post(f'/api/learning/cards/{cid}/verify', json={})
    assert r.json['status'] == 'notFound'


def test_verify_without_provider(client, monkeypatch):
    def _no_session(*a, **k):
        raise AssertionError('must not connect without a bound provider')
    monkeypatch.setattr(mcp_client, 'run_tool_session', _no_session)
    cid = client.post('/api/learning/cards',
                      json={'question': 'Q?', 'answer': 'A.'}).json['id']
    r = client.post(f'/api/learning/cards/{cid}/verify', json={})
    assert r.status_code == 200
    assert r.json['status'] == 'noProvider'


def test_verify_gemini_unsupported(client, monkeypatch):
    session = FakeSession()

    def raise_unsupported(messages, tools):
        raise llm.ToolCallingUnsupported('gemini')
    monkeypatch.setattr(llm, 'chat_with_tools', raise_unsupported)
    cid = _setup_verifiable_card(client, monkeypatch, session)
    r = client.post(f'/api/learning/cards/{cid}/verify', json={})
    assert r.json['status'] == 'providerUnsupported'


def test_verify_provider_failure_is_502(client, monkeypatch):
    session = FakeSession()
    cid = _setup_verifiable_card(client, monkeypatch, session)

    def _boom(server, worker):
        raise RuntimeError('spawn failed')
    monkeypatch.setattr(mcp_client, 'run_tool_session', _boom)
    assert client.post(f'/api/learning/cards/{cid}/verify', json={}).status_code == 502


def test_followup_continues_transcript(client, monkeypatch):
    session = FakeSession()
    prior = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'card'},
        {'role': 'assistant', 'content': '{"verdict": "supports", "summary": "", "citations": []}'},
    ]
    seen = _scripted_llm(monkeypatch, [SimpleNamespace(
        content=json.dumps({'verdict': 'supports', 'summary': 'Follow-up answered.',
                            'citations': []}),
        tool_calls=None,
    )])
    cid = _setup_verifiable_card(client, monkeypatch, session)

    r = client.post(f'/api/learning/cards/{cid}/verify/followup',
                    json={'question': 'What about negative exponents?', 'transcript': prior})
    assert r.status_code == 200
    # The prior transcript was extended, not restarted.
    first_call = seen[0]
    assert first_call[:3] == prior
    assert 'negative exponents' in first_call[3]['content']

    assert client.post(f'/api/learning/cards/{cid}/verify/followup', json={}).status_code == 400


def test_malformed_final_json_retried_then_not_found(client, monkeypatch):
    session = FakeSession()
    _scripted_llm(monkeypatch, [
        SimpleNamespace(content='I think it is fine.', tool_calls=None),
        SimpleNamespace(content='still not json', tool_calls=None),
    ] + [SimpleNamespace(content='nope', tool_calls=None)] * 10)
    cid = _setup_verifiable_card(client, monkeypatch, session)
    r = client.post(f'/api/learning/cards/{cid}/verify', json={})
    assert r.json['status'] == 'notFound'
    assert r.json['case']['verdict'] == 'notFound'
