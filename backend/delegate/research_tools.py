"""The offline-first research toolbox, shared by every chat-shaped surface.

Three tools in a fixed order — search the local library, read what it found,
and only then hand the web to the delegate. It lives in one module rather than
in three copies because **the gate is the feature**: a `delegate` call that
skipped the library is refused here, and the Ideas discussion used to be handed
raw `web_search`/`web_fetch` with no library tools at all, so "offline first"
was a rule exactly one of the three surfaces followed.

Web access exists only where the user asked a question and is waiting for the
answer — the Chat tab, the Ideas discussion, the Writing discussion. Everything
unattended (the nightly research pass, the repo pass) reads the library and the
wiki and nothing else; see `backend/research/agent.py`'s `offline_toolbox`.

It lives in `backend/delegate/` rather than `backend/research/` because the
import graph already runs delegate -> research: `delegate/agent.py` imports
`research.agent` and `research.web` at module level, so a module under
`research/` that imported the delegate would close the cycle at import time.
`research/discuss.py` reaches this through the function-local import block its
`build_toolbox` already has.
"""
import logging

from backend.offline_knowledge import tools as knowledge_tools
from backend.delegate import agent

logger = logging.getLogger(__name__)

DELEGATE_TOOL = {
    'type': 'function',
    'function': {
        'name': 'delegate',
        'description': (
            'Hand a lookup to the web research delegate — quickly for one '
            'fact, or with real depth for a broad question. Use it after local '
            'knowledge is insufficient, immediately when the question is '
            'inherently current, or immediately when the user asked you to '
            'search the web. Do not use it for ordinary conversation or for '
            'recording something.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'task': {
                    'type': 'string',
                    'description': (
                        'What needs looking up, written out in full. The '
                        'delegate cannot see the conversation, so include every '
                        'detail it needs.'
                    ),
                },
                'reason': {
                    # Each value names its own precondition. Left as bare
                    # labels, `local_insufficient` reads as something that can
                    # be asserted about a library nobody searched.
                    'type': 'string',
                    'enum': ['local_insufficient', 'current', 'user_requested'],
                    'description': (
                        'Why the internet is needed. local_insufficient: you '
                        'searched the offline library, read the best result, '
                        'and it does not answer this. current: the question is '
                        'inherently about now — today\'s news, a live price, '
                        'what is on this weekend. user_requested: the user '
                        'explicitly told you to search the web.'
                    ),
                },
            },
            'required': ['task', 'reason'],
        },
    },
}

TOOLS = knowledge_tools.TOOLS + [DELEGATE_TOOL]
NAMES = {tool['function']['name'] for tool in TOOLS}

# The two reasons that legitimately skip the library. Membership in a set
# rather than `!= 'current'`, so a blank or unrecognised reason still falls
# into the gated branch — the safe direction.
_SKIPS_LOCAL = frozenset({'current', 'user_requested'})

# The one copy of the offline-first instruction. Quoted verbatim into the Chat
# tab's decision note, the Writing discussion's, and the Ideas discussion's
# system prompt, because three hand-maintained copies is how the three surfaces
# ended up disagreeing about this in the first place.
RESEARCH_NOTE = """For a factual or reference question, call \
local_knowledge_search with two to four complementary queries in one call: the \
clean entity or title, the full question, and any plausible interpretations \
such as book versus movie. Do not put a guessed answer into a query. Then read \
the strongest one to three results with local_knowledge_read — search results \
and titles are leads, not evidence. When the wording is ambiguous, read the \
plausible meanings and either answer each explicitly or ask which was meant. \
You see the results yourself, so decide whether they actually answer the \
question.

Use delegate only when local evidence is absent, contradictory, stale for the \
question, or too unspecific to support an answer (reason=local_insufficient). \
An inherently current question may go straight there with reason=current.

If the user explicitly asked you to search the web — "search for", "look this \
up online", "check the internet" — go straight to delegate with \
reason=user_requested and skip the library. Use that reason only when they \
actually said so, not because you would rather search the web."""


class ResearchTools:
    """One run's local-first state, plus the nested web delegate.

    An object with a `run_tool`, which is the duck type `research/agent.py`'s
    loop already dispatches to — the same shape `research/code.py`'s CodeTools
    and `research/wiki.py`'s WikiTools use, and for the same reason:
    `searched`/`hits`/`read` are per-run facts. A shared instance would let one
    run's library search unlock a later run's web access, which is precisely
    the gate this class exists to hold.
    """

    def __init__(self, *, checkpoint=None, deadline=None):
        self._checkpoint = checkpoint
        self._deadline = deadline
        self.searched = False
        self.hits = 0
        self.read = False

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        if name == 'delegate':
            return self._run_delegate(args)
        return self._run_local(name, args)

    def _run_local(self, name, args):
        # Through the module object, not a `from ... import run_tool`: the
        # tests patch the module attribute, and so does anything that wants to
        # stand in for the library.
        text, event = knowledge_tools.run_tool(name, args)
        if name == 'local_knowledge_search':
            self.searched = True
            self.hits = event.get('count', 0) if event.get('ok') else 0
        elif name == 'local_knowledge_read' and event.get('ok'):
            self.read = True
        return text, event

    def _run_delegate(self, args):
        skips_local = (args.get('reason') or '').strip() in _SKIPS_LOCAL
        if not skips_local and not self.searched:
            return (
                'Search the offline library first, or mark this as an '
                'inherently current question, or as one the user explicitly '
                'asked you to search the web for.',
                {'tool': 'delegate', 'arg': args.get('task'), 'ok': False,
                 'error': 'offline library has not been searched'},
            )
        if not skips_local and self.hits and not self.read:
            return (
                'Read the strongest local result before deciding it is insufficient.',
                {'tool': 'delegate', 'arg': args.get('task'), 'ok': False,
                 'error': 'offline search result has not been read'},
            )
        result = agent.run((args.get('task') or '').strip(),
                           checkpoint=self._checkpoint, deadline=self._deadline)
        delegate_steps = result.get('steps', [])
        successful = [step for step in delegate_steps if step.get('ok')]
        last_error = next((step.get('error') for step in reversed(delegate_steps)
                           if step.get('error')), None)
        return result.get('summary', ''), {
            'tool': 'delegate',
            'arg': args.get('task'),
            'ok': bool(result.get('summary')) and bool(successful),
            'error': None if successful else last_error,
            'sources': result.get('sources', []),
            'count': len(result.get('sources', [])),
            'delegateSteps': delegate_steps,
            'timedOut': bool(result.get('timedOut')),
        }


def dispatch_for(tools: ResearchTools) -> dict:
    """Every offered name mapped to the one instance holding this run's state."""
    return {name: tools for name in NAMES}


def build(*, checkpoint=None, deadline=None) -> tuple[list[dict], dict, ResearchTools]:
    """(tools, dispatch, state) for one run.

    The triple mirrors `discuss.build_toolbox`'s: `tools` and `dispatch` travel
    together, since a tool the model can see but the dispatch cannot run comes
    back as "Unknown tool" — which reads to the model as a broken tool rather
    than as one it should not have called.
    """
    state = ResearchTools(checkpoint=checkpoint, deadline=deadline)
    return TOOLS, dispatch_for(state), state
