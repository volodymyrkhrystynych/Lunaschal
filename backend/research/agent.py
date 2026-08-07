"""The research tool loop.

A synchronous rewrite of backend/ai/learning_verification.py's `build_case`.
That one is async because MCP is; these tools are plain Python functions, so
the asyncio machinery buys nothing and is dropped entirely.

Two shape decisions worth knowing:

**Tool turns are not streamed.** llama-server assembles OpenAI-shaped
`tool_calls` by running its peg-gemma4 grammar over Gemma 4's native
`<|tool_call>call:NAME{...}` text. Reassembling partial tool-call deltas out of
that parser across chunks is the kind of thing that works in testing and
silently drops an argument in production. Tool turns stay blocking and capped.

**Gathering and answering are separate turns.** This loop only collects
evidence; the caller produces the answer in its own turn, which is what lets
the discussion endpoint stream the prose while the gathering stays blocking.

`checkpoint` is called before every model call and every tool call. That single
hook is where "yield to the user" and "stop when cancelled" both live, which
makes both testable in one place.
"""
import json
import logging

from backend.ai.llm import chat_tool_turn
from backend.ai.mcp_client import serialize_tool_calls
from backend.research import web, wiki

logger = logging.getLogger(__name__)

# Gemma 4 calls one tool per turn far more often than it batches, and the first
# few go on orienting — a wiki_list and two wiki_reads before the web is touched
# at all. At 6 the budget ran out mid-search, which is why the loop never got as
# far as reading a page: a live run that ends in a fetch needs 8.
MAX_TOOL_TURNS = 12
# Tool-selection turns are short by construction — a few tokens of reasoning
# and a call. Capping them keeps the worst-case overlap with an interactive
# chat message to a few seconds rather than the 1800s client timeout.
TURN_MAX_TOKENS = 768
# Per run, across all turns. Stops a loop that keeps finding one more link.
MAX_FETCHES = 12


class Cancelled(RuntimeError):
    """The run was cancelled between steps."""


ALL_TOOLS = web.TOOLS + wiki.TOOLS

# Maps to the owning *module*, not to a bound function: `run_tool` is looked up
# at call time so the handler stays swappable (and patchable in tests) rather
# than frozen at import.
#
# Passed as a parameter rather than read from module scope, because this loop is
# shared: the chat delegate (backend/delegate/) drives it with a different
# toolbox entirely. Defaulting to the research map keeps every existing caller
# unchanged.
_DISPATCH = {
    'web_search': web,
    'web_fetch': web,
    'wiki_list': wiki,
    'wiki_search': wiki,
    'wiki_read': wiki,
}


def _noop(*args, **kwargs) -> None:
    return None


def gather_events(
    system: str,
    user: str,
    *,
    tools: list[dict] | None = None,
    dispatch: dict | None = None,
    on_step=None,
    checkpoint=None,
    max_turns: int = MAX_TOOL_TURNS,
    max_fetches: int = MAX_FETCHES,
):
    """Generator form of the loop: yields ('step', event) as each tool call
    completes, then exactly one ('result', {...}) at the end.

    The SSE discussion endpoint needs this. With the blocking form below, every
    tool event only becomes available *after* gathering finishes — which is
    precisely the silent spinner the events exist to replace.

    `tools` and `dispatch` travel together — a tool the model can see but the
    dispatch can't run comes back as "Unknown tool", which reads to the model as
    a broken tool rather than as one it should not have called.
    """
    yield from _loop(
        system, user, tools=tools, dispatch=dispatch, on_step=on_step,
        checkpoint=checkpoint, max_turns=max_turns, max_fetches=max_fetches,
    )


def gather(
    system: str,
    user: str,
    *,
    tools: list[dict] | None = None,
    dispatch: dict | None = None,
    on_step=None,
    checkpoint=None,
    max_turns: int = MAX_TOOL_TURNS,
    max_fetches: int = MAX_FETCHES,
) -> dict:
    """Blocking form, for the background worker where nothing is watching.

    Returns {messages, steps, sources, turns, truncated}. `messages` is the
    full transcript, ready for the caller's answering turn.
    """
    result: dict = {}
    for kind, payload in gather_events(
        system, user, tools=tools, dispatch=dispatch, on_step=on_step,
        checkpoint=checkpoint, max_turns=max_turns, max_fetches=max_fetches,
    ):
        if kind == 'result':
            result = payload
    return result


def _loop(
    system: str,
    user: str,
    *,
    tools: list[dict] | None = None,
    dispatch: dict | None = None,
    on_step=None,
    checkpoint=None,
    max_turns: int = MAX_TOOL_TURNS,
    max_fetches: int = MAX_FETCHES,
):
    tools = tools if tools is not None else ALL_TOOLS
    dispatch = dispatch if dispatch is not None else _DISPATCH
    on_step = on_step or _noop
    checkpoint = checkpoint or _noop

    messages: list[dict] = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]
    steps: list[dict] = []
    sources: list[dict] = []
    fetches = 0
    turns = 0
    truncated = True

    for _ in range(max_turns):
        checkpoint()
        turns += 1
        try:
            msg, finish_reason = chat_tool_turn(messages, tools, max_tokens=TURN_MAX_TOKENS)
        except Exception as e:
            logger.warning('Research tool turn failed: %s', e)
            steps.append({'tool': None, 'ok': False, 'error': str(e)})
            break

        tool_calls = getattr(msg, 'tool_calls', None)
        if not tool_calls:
            messages.append({'role': 'assistant', 'content': msg.content or ''})
            # A turn stopped at TURN_MAX_TOKENS also arrives with no tool calls.
            # Reading that as "the model is finished" is how a run cut off
            # mid-sentence used to report itself as a complete one.
            truncated = finish_reason == 'length'
            if truncated:
                logger.info('Gathering turn hit the token ceiling; treating as truncated')
            break

        messages.append({
            'role': 'assistant',
            'content': msg.content,
            'tool_calls': serialize_tool_calls(tool_calls),
        })

        for call in tool_calls:
            checkpoint()
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}

            if name in ('web_fetch',) and fetches >= max_fetches:
                text = 'Fetch budget for this run is exhausted. Work with what you have.'
                event = {'tool': name, 'arg': args.get('url'), 'ok': False,
                         'error': 'fetch budget exhausted'}
            else:
                if name == 'web_fetch':
                    fetches += 1
                module = dispatch.get(name)
                if module is None:
                    text, event = f'Unknown tool: {name}', {'tool': name, 'ok': False}
                else:
                    text, event = module.run_tool(name, args)

            steps.append(event)
            on_step(event)
            yield ('step', event)
            # Sources are recorded from what was actually fetched, not from
            # what the model later claims it read.
            if event.get('tool') == 'web_fetch' and event.get('ok'):
                sources.append({'url': event.get('url'), 'title': event.get('title')})
            # A tool that wraps its own nested pass (e.g. the delegate's
            # deep_research) reports what it actually fetched as a `sources`
            # list on its own event rather than as a single url/title pair.
            elif event.get('sources'):
                sources.extend(event['sources'])

            messages.append({
                'role': 'tool',
                'tool_call_id': call.id,
                'content': text,
            })

    yield ('result', {
        'messages': messages,
        'steps': steps,
        'sources': sources,
        'turns': turns,
        # True when the loop hit its turn budget rather than the model deciding
        # it had enough — the caller may want to say so.
        'truncated': truncated,
    })


def make_checkpoint(cancel=None, gate: bool = True):
    """The standard checkpoint: yield to the user, then honour cancellation.

    Order matters — checking cancellation *after* waiting means a run cancelled
    while parked stops at the next step rather than firing one more model call.
    """
    from backend.ai import priority

    def checkpoint():
        if gate:
            priority.wait_for_idle(cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise Cancelled('research run cancelled')

    return checkpoint
