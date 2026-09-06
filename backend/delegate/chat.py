"""Streaming glue for the Chat tab: decide, act, then answer.

Replaces the post-hoc classifier. That one fired *after* the reply had already
been written, on a hand-rolled `should_classify` heuristic, and turned every
exception into a fake low-confidence "conversation" result — so a request that
failed produced no reply, no error and no log line, and looked exactly like a
message the user had never meant as a request at all. Here the decision happens
before the reply, the model makes it, and a failure is a visible error on the
stream.

**The gathering turns are separate from the answer, and their prose is discarded.**
llama-server rebuilds OpenAI `tool_calls` by running a grammar over the model's
native call notation, and reassembling partial tool-call deltas across
chunks is how an argument goes missing in production, so tool-selection turns
stay blocking and capped. They now use the shared bounded loop because local
retrieval normally needs search -> read -> assess. The answer then streams in
its own turn, off a prompt llama-server has already cached.

**The `propose_*` tools are on this turn, not behind the delegate.** They were
behind it, and the cost was exactly the detail the user cares about: the
delegate gets one `task` string and cannot see the conversation, so "by Friday"
and "it's urgent" survived only if the main model remembered to restate them,
and a to-do routinely came out bare. They are small schemas returning one short
string, and this turn already has the conversation, the schedule and the current
date. What stays behind `delegate` is the work whose *output* is large — a
`web_fetch` page dump belongs in a summary, not in the transcript.
"""
import json
import logging
import time
from types import SimpleNamespace

from backend.ai.chat import (
    TIME_PREFIX_NOTE, build_chat_system_prompt, format_now_context, stamp_messages,
)
from backend.ai.llm import chat_stream_events
from backend.chat import compaction
from backend.chat.context import expand_attachments
from backend.delegate import agent, limits, tools as proposal_tools
from backend.lifewiki import tools as life_tools
from backend.lifewiki.tools import LifeTools
from backend.offline_knowledge import tools as knowledge_tools
from backend.research import agent as tool_loop
from backend.research import wiki as wiki_tools

logger = logging.getLogger(__name__)


def _out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline

DELEGATE_TOOL = {
    'type': 'function',
    'function': {
        'name': 'delegate',
        'description': (
            'Hand a lookup to the web research delegate — quickly for one '
            'fact, or with real depth for a broad question. Use it after local '
            'knowledge is insufficient, or immediately when the question is '
            'inherently current. Do not use it for ordinary conversation or '
            'for recording something.'
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
                    'type': 'string',
                    'enum': ['local_insufficient', 'current'],
                    'description': 'Why the internet is needed.',
                },
            },
            'required': ['task', 'reason'],
        },
    },
}

# `wiki_list` is deliberately left out: the index is already in the system
# prompt, so offering a tool that re-fetches it is a round trip for something
# the model can already see.
_LIFE_WIKI_TOOLS = [t for t in wiki_tools.TOOLS
                    if t['function']['name'] in ('wiki_read', 'wiki_search')]
_LIFE_WIKI_NAMES = {t['function']['name'] for t in _LIFE_WIKI_TOOLS}

TOOLS = (knowledge_tools.TOOLS + [DELEGATE_TOOL] + proposal_tools.TOOLS + life_tools.TOOLS
         + _LIFE_WIKI_TOOLS)

DECISION_NOTE = """Right now your only job is to decide which of your tools this \
message needs, if any — your reply comes afterwards, in a separate turn.

Use the propose_ tools to record something the user asked you to record, and \
fill in every detail they actually gave: the deadline, the time, how important \
it is. Do not drop a detail because it was not the main point of the sentence.

Where they clearly meant a detail but were too vague for you to act on it — \
"soon", "before the trip", an event with no date you can work out — call \
ask_user instead of guessing at it. Where they implied nothing, do not ask: \
stage what they said and leave the rest empty.

For a factual or reference question, call local_knowledge_search with two to \
four complementary queries in one call: the clean entity or title, the user's \
full question, and any plausible interpretations such as book versus movie. \
Do not put a guessed answer into a query. Choose from the merged candidates, \
then read the strongest one to three articles with local_knowledge_read. Search \
results and titles are leads, not evidence. When the wording is ambiguous, read \
the plausible meanings and either answer each explicitly or ask the user which \
they meant. You see the local results yourself, so decide whether they actually \
answer the user's question.

Use delegate only when local evidence is absent, contradictory, stale for the \
question, or too unspecific to support an answer. An inherently current question \
may go directly to delegate with reason=current.

Use search_conversations, search_journal or read_day when the answer is \
something the user already told you or wrote down and you cannot see it in \
this conversation. Their own record first, the web second.

Use wiki_read when one of your own notes about them is listed above and the \
answer turns on what is actually in it, rather than on its title.

Use remember for a fact about them that will still be true next month. Not for \
anything that happens once, and never for something already in your notes.

If the message needs none of this, write nothing at all: anything you type in \
this turn is discarded."""

ANSWER_INSTRUCTION = (
    'Reply to the user now, in your own voice. If the knowledge tools or web delegate found '
    'information, use it to actually answer the question — state the facts it '
    'found, in full, do not just gesture at having looked something up. If you '
    'staged anything, mention that in passing — a confirmation card is already '
    'showing, so do not repeat its contents back and do not say it is saved, '
    'because they still have to confirm it. If you asked for clarification, put '
    'that question to them plainly and do not pretend you staged the thing it '
    'is about. If something could not be done, say so plainly rather than '
    'glossing over it.\n\n'
    # `remember` writes to the assistant's own notes, and its predecessor was
    # removed partly because it made every reply report the write. The tool's
    # own return text says this too; it is repeated here because this is the
    # instruction closest to the text actually being generated.
    'If you noted something down for yourself this turn, do not mention it. '
    'That is bookkeeping, not an answer.\n\n'
    # The tools were on the previous turn and are gone from this one, so a call
    # written here reaches nothing: llama-server only reconstructs `tool_calls`
    # from a request that carried `tools=`, and without that the raw notation
    # lands in `content` and renders as the reply. It has been observed doing
    # exactly that — repeating every call it had already made, as the answer.
    'You have no tools in this turn. Write the reply itself, in prose: anything '
    'shaped like a tool call is discarded, and whatever you already staged is '
    'staged.'
)


def _system_prompt(messages: list[dict], system_prompt: str = '') -> str:
    system = system_prompt or build_chat_system_prompt()
    system = f'{system}\n\n{format_now_context()}'
    if any(m.get('createdAt') for m in messages):
        system = f'{system}\n\n{TIME_PREFIX_NOTE}'
    return system


def _main_dispatch(*, life: LifeTools, life_wiki, checkpoint, deadline):
    """Bind conversation-scoped tools and the nested web delegate for one run."""
    local_state = {'searched': False, 'hits': 0, 'read': False}
    action_calls: set[tuple[str, str]] = set()

    def run_local(name, args):
        text, event = knowledge_tools.run_tool(name, args)
        if name == 'local_knowledge_search':
            local_state['searched'] = True
            local_state['hits'] = event.get('count', 0) if event.get('ok') else 0
        elif name == 'local_knowledge_read' and event.get('ok'):
            local_state['read'] = True
        return text, event

    def run_delegate(_name, args):
        if args.get('reason') != 'current' and not local_state['searched']:
            return (
                'Search the offline library first, or mark this as an inherently current question.',
                {'tool': 'delegate', 'arg': args.get('task'), 'ok': False,
                 'error': 'offline library has not been searched'},
            )
        if (args.get('reason') != 'current' and local_state['hits']
                and not local_state['read']):
            return (
                'Read the strongest local result before deciding it is insufficient.',
                {'tool': 'delegate', 'arg': args.get('task'), 'ok': False,
                 'error': 'offline search result has not been read'},
            )
        result = agent.run((args.get('task') or '').strip(), checkpoint=checkpoint,
                           deadline=deadline)
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

    def run_proposal(name, args):
        # A multi-turn gather can reconsider after seeing a search result. It
        # must not perform the exact same write/stage twice while doing so — a
        # safety property the old one-shot decision turn got for free.
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in action_calls:
            return 'That exact action already ran during this reply.', {
                'tool': name, 'arg': args, 'ok': False,
                'error': 'duplicate tool call ignored',
            }
        action_calls.add(key)
        return proposal_tools.run_tool(name, args)

    dispatch = {
        'local_knowledge_search': SimpleNamespace(run_tool=run_local),
        'local_knowledge_read': SimpleNamespace(run_tool=run_local),
        'delegate': SimpleNamespace(run_tool=run_delegate),
    }
    dispatch.update({name: life_wiki for name in _LIFE_WIKI_NAMES})
    dispatch.update({name: life for name in life_tools.TOOL_NAMES})
    proposal_dispatch = SimpleNamespace(run_tool=run_proposal)
    dispatch.update({
        tool['function']['name']: proposal_dispatch for tool in proposal_tools.TOOLS
    })
    return dispatch


def stream_reply(messages: list[dict], system_prompt: str = '', *,
                 tools_enabled: bool = True, checkpoint=None,
                 conversation_id: str | None = None):
    """Yields ('step', event) as each tool call finishes, then ('thinking',
    delta) and ('content', delta) as the reply streams, then one
    ('done', {steps, sources, proposals, truncated}).

    Steps and proposals go out on the `done` payload as well as live, because
    the browser persists them onto the assistant message's metadata — a reload
    has to redraw the same trace, and the live events are gone by then.

    `tools_enabled=False` skips the decision turn entirely, for the callers with
    no UI to confirm anything in: the voice listener, task nudges and the
    morning check-in all speak their replies aloud, where a staged card is
    invisible and the decision turn is pure added latency before the user hears
    a word.

    `conversation_id` is only used to keep the recall tools from returning this
    conversation's own messages back to it — they are already in the transcript
    verbatim, and re-reading them would spend the result budget on nothing.
    Callers without one (voice, nudges, Writing discussions) pass none and get
    unfiltered hits, which is correct: they are not in a conversation that could
    be returned.

    The chat deadline is established here rather than by the caller, so every
    caller gets it — the voice listener waiting on a spoken reply has more
    reason to want one than the Chat tab does, not less. It is the outer bound
    the delegate's own budget and its nested deep pass are both clamped to.
    """
    deadline = limits.chat_deadline()
    timed_out = False
    system = _system_prompt(messages, system_prompt)
    messages, rolling_context = compaction.compact_for_prompt(
        messages, conversation_id,
    )
    context_blocks = [
        compaction.handoff_context(conversation_id),
        rolling_context,
        compaction.evidence_context(conversation_id),
    ]
    if any(context_blocks):
        system += '\n\n' + '\n\n'.join(x for x in context_blocks if x)
    # Photos become text before stamping, not after: stamp_messages flattens a
    # message to `[today 21:58] <content>`, so anything that must reach the model
    # has to already be in `content` by then.
    conversation = [{'role': 'system', 'content': system}] + stamp_messages(
        expand_attachments(messages)
    )

    steps: list[dict] = []
    sources: list[dict] = []
    proposals: list[dict] = []
    life = LifeTools(conversation_id)
    # Scoped to the life wiki, so a chat can never surface a note about a
    # codebase — and, in the other direction, the Ideas agent's default
    # WikiTools scope keeps the user's life out of a research turn.
    life_wiki = wiki_tools.WikiTools(kind=wiki_tools.LIFE_KIND)

    evidence: list[dict] = []
    if tools_enabled:
        gathered: dict = {}
        dispatch = _main_dispatch(
            life=life, life_wiki=life_wiki, checkpoint=checkpoint,
            deadline=deadline,
        )
        for kind, payload in tool_loop.gather_events(
                initial_messages=conversation + [
                    {'role': 'user', 'content': DECISION_NOTE},
                ],
                tools=TOOLS, dispatch=dispatch, checkpoint=checkpoint,
                max_turns=6, max_fetches=0, deadline=deadline,
                ignore_unknown_tools=True):
            if kind == 'step':
                steps.append(payload)
                if payload.get('proposal'):
                    proposals.append(payload['proposal'])
                yield ('step', payload)
            else:
                gathered = payload
        conversation = gathered.get('messages', conversation)
        sources = gathered.get('sources', [])
        evidence = gathered.get('evidence', [])

    conversation.append({'role': 'user', 'content': ANSWER_INSTRUCTION})
    truncated = False
    for kind, delta in chat_stream_events(conversation):
        # Rides out on `done` rather than as its own live event: it is only
        # known once the stream ends, and it is a fact *about* the reply, which
        # is what the browser persists alongside the steps.
        if kind == 'truncated':
            truncated = True
            continue
        yield (kind, delta)
        if _out_of_time(deadline):
            # Stop consuming the stream and keep what arrived. Deliberately no
            # salvage rewrite here, unlike the research tools: this text is
            # already the answer, and the user has been reading it as it
            # arrived — regenerating it from the top would replace what they
            # have with something they have to re-read.
            timed_out = True
            logger.info('Reply hit the chat deadline mid-stream; keeping what streamed')
            break

    yield ('done', {'steps': steps, 'sources': sources, 'evidence': evidence,
                    'proposals': proposals,
                    'truncated': truncated, 'timedOut': timed_out})
