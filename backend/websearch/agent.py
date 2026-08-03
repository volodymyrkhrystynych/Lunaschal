"""The web-search chat tool loop.

Two shape decisions worth knowing:

**Tool turns are not streamed.** llama-server assembles OpenAI-shaped
`tool_calls` by running its peg-gemma4 grammar over Gemma 4's native
`<|tool_call>call:NAME{...}` text. Reassembling partial tool-call deltas out of
that parser across chunks is the kind of thing that works in testing and
silently drops an argument in production. Tool turns stay blocking and capped.

**Gathering and answering are separate turns.** This loop only collects
evidence; the caller produces the answer in its own turn, which is what lets
the streaming route send the prose while the gathering stays blocking.

Unlike a one-shot research discussion, this loop takes the **full running
message list** (system prompt + prior turns + the new user message) rather
than a single flattened question — the web-search chat tab is an ongoing
multi-turn conversation, not a single Q&A.
"""
import json

from backend.ai.llm import chat_with_tools
from backend.ai.mcp_client import serialize_tool_calls
from backend.websearch import tools as web

MAX_TOOL_TURNS = 6
# Tool-selection turns are short by construction — a few tokens of reasoning
# and a call. Capping them keeps a multi-turn run from ballooning.
TURN_MAX_TOKENS = 768
# Per run, across all turns. Stops a loop that keeps finding one more link.
MAX_FETCHES = 12

ALL_TOOLS = web.TOOLS


def _noop(*args, **kwargs) -> None:
    return None


def gather(
    system: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    on_step=None,
    max_turns: int = MAX_TOOL_TURNS,
    max_fetches: int = MAX_FETCHES,
) -> dict:
    """Blocking form of gather_events, for callers (tests, one-off scripts)
    that don't need per-step events. Returns {messages, steps, sources, turns,
    truncated}."""
    result: dict = {}
    for kind, payload in gather_events(
        system, messages, tools=tools, on_step=on_step,
        max_turns=max_turns, max_fetches=max_fetches,
    ):
        if kind == 'result':
            result = payload
    return result


def gather_events(
    system: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    on_step=None,
    max_turns: int = MAX_TOOL_TURNS,
    max_fetches: int = MAX_FETCHES,
):
    """Generator form of the loop: yields ('step', event) as each tool call
    completes, then exactly one ('result', {...}) at the end.

    The streaming route needs this: with a blocking form, every tool event
    only becomes available *after* gathering finishes — which is precisely
    the silent spinner the events exist to replace.
    """
    tools = tools if tools is not None else ALL_TOOLS
    on_step = on_step or _noop

    conversation: list[dict] = [{'role': 'system', 'content': system}] + list(messages)
    steps: list[dict] = []
    sources: list[dict] = []
    fetches = 0
    turns = 0
    truncated = True

    for _ in range(max_turns):
        turns += 1
        try:
            msg = chat_with_tools(conversation, tools, max_tokens=TURN_MAX_TOKENS)
        except Exception as e:
            steps.append({'tool': None, 'ok': False, 'error': str(e)})
            break

        tool_calls = getattr(msg, 'tool_calls', None)
        if not tool_calls:
            conversation.append({'role': 'assistant', 'content': msg.content or ''})
            truncated = False
            break

        conversation.append({
            'role': 'assistant',
            'content': msg.content,
            'tool_calls': serialize_tool_calls(tool_calls),
        })

        for call in tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}

            if name == 'web_fetch' and fetches >= max_fetches:
                text = 'Fetch budget for this run is exhausted. Work with what you have.'
                event = {'tool': name, 'arg': args.get('url'), 'ok': False,
                         'error': 'fetch budget exhausted'}
            else:
                if name == 'web_fetch':
                    fetches += 1
                text, event = web.run_tool(name, args)

            steps.append(event)
            on_step(event)
            yield ('step', event)
            # Sources are recorded from what was actually fetched, not from
            # what the model later claims it read.
            if event.get('tool') == 'web_fetch' and event.get('ok'):
                sources.append({'url': event.get('url'), 'title': event.get('title')})

            conversation.append({
                'role': 'tool',
                'tool_call_id': call.id,
                'content': text,
            })

    yield ('result', {
        'messages': conversation,
        'steps': steps,
        'sources': sources,
        'turns': turns,
        # True when the loop hit its turn budget rather than the model deciding
        # it had enough — the caller may want to say so.
        'truncated': truncated,
    })
