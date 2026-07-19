"""Minimal MCP client bridge for evidence providers.

The mcp SDK is asyncio-only and Flask is sync, so each verification request
runs one asyncio.run() around a full session lifecycle: connect → initialize →
worker(session) → teardown (context managers guarantee stdio subprocess
cleanup). Latency is fine — verification is user-initiated.
"""
import asyncio
import json


def mcp_tools_to_openai(tools) -> list[dict]:
    """Map MCP tool descriptors to OpenAI chat-completions tool definitions."""
    return [
        {
            'type': 'function',
            'function': {
                'name': t.name,
                'description': t.description or '',
                'parameters': t.inputSchema or {'type': 'object', 'properties': {}},
            },
        }
        for t in tools
    ]


def serialize_tool_calls(tool_calls) -> list[dict]:
    """Tool calls from a completion message → JSON-serializable transcript form."""
    return [
        {
            'id': tc.id,
            'type': 'function',
            'function': {'name': tc.function.name, 'arguments': tc.function.arguments},
        }
        for tc in tool_calls
    ]


def tool_result_text(result) -> str:
    """Flatten an MCP call_tool result to plain text for the LLM transcript."""
    parts = []
    for item in getattr(result, 'content', None) or []:
        text = getattr(item, 'text', None)
        if text:
            parts.append(text)
    return '\n'.join(parts) or '(no text content)'


async def _connect_and_run(server: dict, worker):
    from mcp import ClientSession, StdioServerParameters

    if server['transport'] == 'stdio':
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=server['command'],
            args=json.loads(server['args']) if server['args'] else [],
            env=json.loads(server['env']) if server['env'] else None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await worker(session)

    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(server['url']) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await worker(session)


def run_tool_session(server, worker):
    """Run `async worker(session)` against the given mcp_servers row."""
    return asyncio.run(_connect_and_run(dict(server), worker))


def test_server(server) -> dict:
    """Connectivity check for the Settings/Folders UI: connect + list tools."""
    async def worker(session):
        result = await session.list_tools()
        return [t.name for t in result.tools]

    try:
        tools = run_tool_session(server, worker)
        return {'ok': True, 'tools': tools}
    except Exception as e:
        return {'ok': False, 'tools': [], 'error': str(e)}
