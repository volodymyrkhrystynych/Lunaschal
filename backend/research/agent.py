"""The research tool loop.

A synchronous rewrite of backend/ai/learning_verification.py's `build_case`.
That one is async because MCP is; these tools are plain Python functions, so
the asyncio machinery buys nothing and is dropped entirely.

Two shape decisions worth knowing:

**Tool turns are not streamed.** llama-server assembles OpenAI-shaped
`tool_calls` by running a grammar over the model's own native call notation,
choosing the parser from its chat template. Reassembling partial tool-call
deltas out of that parser across chunks is the kind of thing that works in
testing and silently drops an argument in production. Tool turns stay blocking
and capped.

**Gathering and answering are separate turns.** This loop only collects
evidence; the caller produces the answer in its own turn, which is what lets
the discussion endpoint stream the prose while the gathering stays blocking.

`checkpoint` is called before every model call and every tool call. That single
hook is where "yield to the user" and "stop when cancelled" both live, which
makes both testable in one place.

`deadline` is checked in the same two places, and is deliberately *not* part of
`checkpoint`: a checkpoint raises, and running out of time is not a failure.
Everything bounding this loop before it was a count -- turns, fetches, tokens --
so a run that took an hour was inside every limit it had. Hitting the deadline
stops gathering and reports `timed_out`; what to do with a half-gathered run is
the caller's decision, and both callers answer the same way (see
backend/delegate/deep_research.py's salvage synthesis).
"""
import json
import logging
import time

from backend.ai.llm import chat_tool_turn
from backend.ai.mcp_client import serialize_tool_calls
from backend.research import web, wiki

logger = logging.getLogger(__name__)

# Measured against Gemma 4, which called one tool per turn far more often than
# it batched, and spent the first few orienting — a wiki_list and two wiki_reads
# before the web was touched at all. At 6 the budget ran out mid-search, which is
# why the loop never got as far as reading a page: a live run that ends in a
# fetch needed 8. Kept at 12 across the swap to Qwen3.6 because it is a ceiling,
# not a target — a model that batches its calls simply finishes sooner. Worth
# re-observing on a live run: if Qwen consistently ends well under it, the
# headroom is free, and if it ever hits 12 the number was tuned for the wrong
# model.
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


def deadline_from(seconds, outer: float | None = None) -> float | None:
    """A monotonic deadline `seconds` from now, never later than `outer`.

    The one place "an inner budget cannot outlive the outer one" is written
    down. A nested deep-research pass inside a chat reply must not still be
    searching after the reply's own budget is spent, and clamping at each call
    site is how one of them would end up not clamping.

    A non-positive or unusable `seconds` means "no deadline of my own" and
    returns `outer` unchanged, so disabling one budget cannot silently remove
    the one wrapping it.
    """
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return outer
    if seconds <= 0:
        return outer
    own = time.monotonic() + seconds
    return own if outer is None else min(own, outer)


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
    deadline: float | None = None,
):
    """Generator form of the loop: yields ('step', event) as each tool call
    completes, then exactly one ('result', {...}) at the end.

    The SSE discussion endpoint needs this. With the blocking form below, every
    tool event only becomes available *after* gathering finishes — which is
    precisely the silent spinner the events exist to replace.

    `tools` and `dispatch` travel together — a tool the model can see but the
    dispatch can't run comes back as "Unknown tool", which reads to the model as
    a broken tool rather than as one it should not have called.

    `deadline` is a `time.monotonic()` absolute (see `deadline_from`), or None
    for the old unbounded behaviour.
    """
    yield from _loop(
        system, user, tools=tools, dispatch=dispatch, on_step=on_step,
        checkpoint=checkpoint, max_turns=max_turns, max_fetches=max_fetches,
        deadline=deadline,
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
    deadline: float | None = None,
) -> dict:
    """Blocking form, for the background worker where nothing is watching.

    Returns {messages, steps, sources, turns, truncated, timed_out}. `messages`
    is the full transcript, ready for the caller's answering turn.
    """
    result: dict = {}
    for kind, payload in gather_events(
        system, user, tools=tools, dispatch=dispatch, on_step=on_step,
        checkpoint=checkpoint, max_turns=max_turns, max_fetches=max_fetches,
        deadline=deadline,
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
    deadline: float | None = None,
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
    timed_out = False

    def out_of_time() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    for _ in range(max_turns):
        if out_of_time():
            timed_out = True
            logger.info('Gathering hit its deadline after %d turn(s)', turns)
            break
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
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}

            if out_of_time():
                # Every call still gets a tool message, including the ones that
                # will never run: an assistant turn whose tool_calls have no
                # replies is a malformed exchange, and this transcript is
                # exactly what the caller's salvage turn is handed.
                timed_out = True
                text = 'Out of time for this run. Work with what you already have.'
                event = {'tool': name, 'arg': args.get('query') or args.get('url'),
                         'ok': False, 'error': 'out of time'}
            else:
                checkpoint()
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

        if timed_out:
            break

    yield ('result', {
        'messages': messages,
        'steps': steps,
        'sources': sources,
        'turns': turns,
        # True when the loop hit its turn budget rather than the model deciding
        # it had enough — the caller may want to say so.
        'truncated': truncated,
        # A stricter case of truncated: the wall clock ran out, so what is in
        # `messages` is everything this run will ever have. The caller is
        # expected to salvage an answer from it rather than report a failure.
        'timed_out': timed_out,
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
