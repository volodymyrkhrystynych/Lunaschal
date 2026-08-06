"""The chat delegate: one tool loop, spawned by the main chat model.

The main chat model carries exactly one tool — `delegate({task})` — so its only
decision is the binary "does this need a hand-off". Every capability lives in
*this* loop's toolbox instead, which is what keeps the main chat's context from
growing a token of schema every time the app learns to do something new.

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
background research pass.
"""
import logging

from backend.delegate import tools as proposals
from backend.research import agent, web

logger = logging.getLogger(__name__)

# Enough for search → read → propose with room to recover from one failure.
# Above this the user is waiting on a spinner longer than the reply is worth.
MAX_TOOL_TURNS = 6
# Per run. A delegate is answering one chat message, not writing an article.
MAX_FETCHES = 4

ALL_TOOLS = proposals.TOOLS + web.TOOLS

DISPATCH = {
    'web_search': web,
    'web_fetch': web,
    **{name: proposals for name in (
        'propose_task',
        'propose_calendar_event',
        'propose_calorie_log',
        'propose_note_to_self',
        'propose_flashcards',
    )},
}

SYSTEM_PROMPT = """You are Lunaschal's delegate. The assistant handed you one \
task from a chat with the user, because it needed something done rather than \
just said.

You have two kinds of tool.

The propose_* tools stage something for the user to confirm — a to-do, a \
calendar event, a calorie entry, a note to self, a set of flashcards. They save \
nothing on their own; the user gets a card to click. Stage what the task \
actually asked for and no more. Do not stage a calorie count the user did not \
give, and do not stage a note to self when they have not said what the lesson is.

web_search and web_fetch really do read the internet. Use them when the task \
depends on current or specific information you are not confident about, and \
read a page rather than answering from snippets.

Work in as few steps as the task needs, then stop and write one short plain \
summary of what you did — what you staged, what you found, and anything you \
could not do. That summary is all the assistant will see, so leave nothing \
important out of it and do not claim anything you did not actually do."""


def run_events(task: str, *, checkpoint=None, max_turns: int = MAX_TOOL_TURNS,
               max_fetches: int = MAX_FETCHES):
    """Yield ('step', event) per tool call, then one ('result', {...}).

    The result adds `proposals` and `summary` to what the shared loop returns:
    `proposals` are pulled off the step events rather than parsed back out of
    the model's prose, and `summary` is the model's own closing message — the
    only part of a delegate run the main chat model is shown.
    """
    result: dict = {}
    for kind, payload in agent.gather_events(
        SYSTEM_PROMPT, task,
        tools=ALL_TOOLS, dispatch=DISPATCH, checkpoint=checkpoint,
        max_turns=max_turns, max_fetches=max_fetches,
    ):
        if kind == 'result':
            result = payload
        else:
            yield (kind, payload)

    steps = result.get('steps', [])
    yield ('result', {
        'steps': steps,
        'sources': result.get('sources', []),
        'proposals': [s['proposal'] for s in steps if s.get('proposal')],
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
    staged = [s for s in steps if s.get('proposal')]
    if staged:
        return (
            'Staged for the user to confirm: '
            + '; '.join(s.get('arg') or s.get('tool', '') for s in staged)
            + '. (The delegate ran out of room before summarising.)'
        )
    if steps:
        return ('The delegate tried but did not finish — '
                f'{len(steps)} step(s) ran and nothing was staged.')
    return 'The delegate could not do anything with that task.'
