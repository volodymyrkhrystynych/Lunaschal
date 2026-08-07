"""The delegate's deep-research tool: one more relentless pass than web_search.

web_search/web_fetch (backend/research/web.py) are single-shot — the delegate's
own loop gives them a six-turn, four-fetch budget tuned for chat latency. This
is the other end of that trade: a nested instance of the same shared loop
(backend/research/agent.py), with a budget an order of magnitude larger and a
system prompt that tells it to chase multiple angles rather than stop at the
first result — then a separate synthesis turn writes up an actual answer
instead of a search log, mirroring how backend/research/discuss.py separates
gathering from answering.

From the outer delegate loop's point of view this is still just one tool call
that returns (text, event) like every other — it only runs longer. That is
deliberate: reusing the shared loop means the SSRF guard, the fetch budget, and
the priority checkpoint all come for free, instead of a second copy of any of
them.

`checkpoint` is not read from module scope — backend/delegate/agent.py binds
the delegate's own checkpoint into this tool's dispatch entry for each run
(via functools.partial), which is what lets the nested pass cooperate with the
same "yield to the user" gate as everything else in the loop, without
research/agent.py's `_loop` needing to know this tool is any different from
the rest of the toolbox.
"""
from backend.ai.llm import chat_messages
from backend.research import agent, web

# An order of magnitude past the delegate's own budget (MAX_TOOL_TURNS=6,
# MAX_FETCHES=4) and past the Ideas background research pass (12/12) — this is
# meant to actually be deep, not just less shallow. Worst case is roughly
# MAX_DEEP_TURNS turns of generation-plus-tool-call, which stays well inside
# the 1800s blocking-call timeout backend/ai/llm.py allows.
MAX_DEEP_TURNS = 30
MAX_DEEP_FETCHES = 20

GATHER_SYSTEM_PROMPT = """You are gathering information to answer one question \
as thoroughly as possible — this is deep research, not a quick lookup. You have \
web_search and web_fetch.

Search from more than one angle before concluding: a single query rarely \
surfaces the whole picture. Read more than one source, and when something is \
unclear, contradictory, or only partly answered, search again rather than \
settling for the first thing you found. Keep digging until you are genuinely \
confident in the answer or the budget runs out, whichever comes first.

When you have gathered enough, reply with a short note saying so rather than \
calling another tool."""

ANSWER_INSTRUCTION = (
    'Write up what you found. Answer the question directly and in full — state '
    'the actual facts, numbers, names or dates, and say which source each came '
    'from. If something you looked for could not be confirmed, say so plainly '
    'rather than papering over the gap. This goes straight to the person who '
    'asked, so give them the answer itself, not a description of your search '
    'process.'
)

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'deep_research',
            'description': (
                'Research a question thoroughly: many searches and page reads '
                'across multiple angles, not a quick lookup. Takes noticeably '
                'longer than web_search, so reach for it only when the '
                'question is broad or open-ended enough that a single search '
                'would leave real gaps — not for a simple fact.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': (
                            'The question to research, written out in full — '
                            'the researcher cannot see the conversation.'
                        ),
                    },
                },
                'required': ['query'],
            },
        },
    },
]


def run_tool(name: str, args: dict, checkpoint=None) -> tuple[str, dict]:
    """Run a nested deep-research pass. Returns (text for the model, event for the UI).

    Never raises: a failed or empty pass is information the delegate's own
    model can act on, matching the contract every other tool in the toolbox
    follows.
    """
    query = (args.get('query') or '').strip() if isinstance(args, dict) else ''
    if not query:
        return (
            'Could not start deep research: no question was given.',
            {'tool': 'deep_research', 'ok': False, 'error': 'a question is required'},
        )

    result = agent.gather(
        GATHER_SYSTEM_PROMPT, query,
        tools=web.TOOLS, dispatch={'web_search': web, 'web_fetch': web},
        checkpoint=checkpoint, max_turns=MAX_DEEP_TURNS, max_fetches=MAX_DEEP_FETCHES,
    )
    sources = result.get('sources') or []

    messages = result.get('messages') or []
    report = (chat_messages(messages + [{'role': 'user', 'content': ANSWER_INSTRUCTION}])
              or '').strip()

    if not report:
        return (
            'Deep research ran but produced no answer.',
            {'tool': 'deep_research', 'arg': query, 'ok': False,
             'error': 'no answer was produced', 'sources': sources},
        )

    return (
        report,
        {'tool': 'deep_research', 'arg': query, 'ok': True,
         'count': len(sources), 'turns': result.get('turns', 0), 'sources': sources},
    )
