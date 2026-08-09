"""The chat research delegate: one tool loop, spawned by the main chat model.

The toolbox is split along one line: **tools that need conversation context live
in the main chat; tools that generate volume live here.** The `propose_*` tools
used to live in this loop, and that was the mistake — a delegate is handed a
single `task` string and cannot see the conversation, so every detail the main
model forgot to restate ("by Friday", "it's urgent", "the thing I mentioned
earlier") was simply gone by the time a to-do got staged. Those tools are cheap
schemas that return one short string, so they cost the main chat almost nothing
and gain the whole conversation. See backend/delegate/chat.py.

What stays here is the opposite kind: `web_search`, `web_fetch` and
`deep_research` are multi-step, retry-prone, and their results are enormous. The
point of delegating them is that only the closing summary crosses back — a
`web_fetch` page dump in the persistent chat transcript would be paid for on
every subsequent turn of the conversation.

This is deliberately not a fourth tool loop. It drives
backend/research/agent.py's, with its own tools and dispatch passed in — that
one already has the two properties this needs and the retired
backend/websearch/agent.py lacked: a `checkpoint()` before every model and tool
call (so a delegate run parks while the user is waiting on something else), and
`finish_reason` inspection (so a turn cut off at the token ceiling is not read
as the model deciding it was finished).

The budget is small on purpose. A delegate run sits between the user pressing
Enter and their first token, so its worst case is latency the user is watching:
six turns of at most 768 tokens each, with the fetch budget below that of a
background research pass. `deep_research` (backend/delegate/deep_research.py)
is the one deliberate exception — it is a single tool call from this loop's own
point of view, but internally runs a second, much deeper pass, because a
question that actually needs that depth is worth the wait it costs.
"""
import functools
import logging
from types import SimpleNamespace

from backend.ai.chat import format_now_context
from backend.delegate import deep_research
from backend.research import agent, web

logger = logging.getLogger(__name__)

# Enough for search → read → read with room to recover from one failure. Above
# this the user is waiting on a spinner longer than the reply is worth.
MAX_TOOL_TURNS = 6
# Per run. A delegate is answering one chat message, not writing an article.
MAX_FETCHES = 4

ALL_TOOLS = web.TOOLS + deep_research.TOOLS

DISPATCH = {
    'web_search': web,
    'web_fetch': web,
    'deep_research': deep_research,
}

SYSTEM_PROMPT = """You are Lunaschal's research delegate. The assistant handed \
you one task from a chat with the user, because it needed something looked up \
rather than just said.

web_search and web_fetch really do read the internet. Use them when the task \
depends on current or specific information you are not confident about, and \
read a page rather than answering from snippets.

deep_research also reads the internet, but far more of it: many searches and \
page reads across multiple angles instead of one or two, and it takes \
noticeably longer as a result. Reach for it only when the task is broad or \
open-ended enough that a single web_search would leave real gaps — not for a \
quick fact, which web_search already handles.

Work in as few steps as the task needs, then stop and write a closing summary. \
That summary is all the assistant will see, so leave nothing important out of \
it and do not claim anything you did not actually do.

Your summary IS the answer: state the actual facts, numbers, names or dates you \
found, not a description of your search process ("found the release date: March \
3, 2025", not "searched for the release date and found some results"). If you \
could not find something, say so plainly rather than padding the summary."""


def _system_prompt() -> str:
    """The prompt with today's date on it.

    Built per call, not at import: a process that outlives midnight would
    otherwise keep telling the model it is yesterday. The main chat has carried
    `format_now_context()` all along — this loop never did, so a task like
    "what's on this weekend" reached a model with no idea when now was.
    """
    return f'{SYSTEM_PROMPT}\n\n{format_now_context()}'


def run_events(task: str, *, checkpoint=None, max_turns: int = MAX_TOOL_TURNS,
               max_fetches: int = MAX_FETCHES):
    """Yield ('step', event) per tool call, then one ('result', {...}).

    The result adds `summary` to what the shared loop returns: the model's own
    closing message, and the only part of a delegate run the main chat model is
    ever shown.
    """
    # deep_research runs its own nested pass, which needs the same checkpoint
    # this loop got — not the module-level DISPATCH entry, which the shared
    # loop calls with no checkpoint at all. Rebuilding this dict per call is
    # what lets each run bind its own checkpoint without research/agent.py's
    # `_loop` needing to know this tool is any different from the rest.
    dispatch = {
        **DISPATCH,
        'deep_research': SimpleNamespace(
            run_tool=functools.partial(deep_research.run_tool, checkpoint=checkpoint)),
    }

    result: dict = {}
    for kind, payload in agent.gather_events(
        _system_prompt(), task,
        tools=ALL_TOOLS, dispatch=dispatch, checkpoint=checkpoint,
        max_turns=max_turns, max_fetches=max_fetches,
    ):
        if kind == 'result':
            result = payload
        else:
            yield (kind, payload)

    yield ('result', {
        'steps': result.get('steps', []),
        'sources': result.get('sources', []),
        'summary': _summary(result),
        'truncated': result.get('truncated', False),
    })


def run(task: str, **kwargs) -> dict:
    """Blocking form, for tests and any caller not streaming steps."""
    result: dict = {}
    for kind, payload in run_events(task, **kwargs):
        if kind == 'result':
            result = payload
    return result


def _summary(result: dict) -> str:
    """The delegate's closing message, or an honest stand-in for it.

    A loop that ends on its turn budget never writes one, and a run whose every
    tool failed has nothing to say — in both cases the main model must be told
    that rather than handed an empty string it will paper over.
    """
    messages = result.get('messages') or []
    last = messages[-1] if messages else {}
    if last.get('role') == 'assistant' and not last.get('tool_calls'):
        text = (last.get('content') or '').strip()
        if text and not result.get('truncated'):
            return text

    steps = result.get('steps') or []
    if steps:
        return ('The delegate ran out of room before it could answer — '
                f'{len(steps)} step(s) ran but it never summarised what it found.')
    return 'The delegate could not look anything up for that task.'
