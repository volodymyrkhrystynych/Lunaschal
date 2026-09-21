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
from backend.delegate import limits, research_tools, tools as proposal_tools
from backend.lifewiki import tools as life_tools
from backend.lifewiki.tools import LifeTools
from backend.research import agent as tool_loop
from backend.research import wiki as wiki_tools
from backend.writing import tools as writing_tools

logger = logging.getLogger(__name__)


def _out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline

# The research toolbox moved to backend/delegate/research_tools.py so the
# Ideas and Writing discussions could mount the same three tools with the
# same gate. Aliased rather than re-imported at every reference: the name
# is what the tests and the docs call it.
DELEGATE_TOOL = research_tools.DELEGATE_TOOL

# `wiki_list` is deliberately left out: the index is already in the system
# prompt, so offering a tool that re-fetches it is a round trip for something
# the model can already see.
_LIFE_WIKI_TOOLS = [t for t in wiki_tools.TOOLS
                    if t['function']['name'] in ('wiki_read', 'wiki_search')]
_LIFE_WIKI_NAMES = {t['function']['name'] for t in _LIFE_WIKI_TOOLS}

TOOLS = (research_tools.TOOLS + proposal_tools.TOOLS + life_tools.TOOLS
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

{research_note}

Use search_conversations, search_journal or read_day when the answer is \
something the user already told you or wrote down and you cannot see it in \
this conversation. Their own record first, the web second.

Use wiki_read when one of your own notes about them is listed above and the \
answer turns on what is actually in it, rather than on its title.

Use remember for a fact about them that will still be true next month. Not for \
anything that happens once, and never for something already in your notes.

If the message needs none of this, write nothing at all: anything you type in \
this turn is discarded."""

# One copy of the offline-first rule, shared with the Ideas and Writing
# discussions: this turn's wording is the only thing that decides whether the
# library gets searched before the web, and three hand-kept copies drift.
DECISION_NOTE = DECISION_NOTE.format(research_note=research_tools.RESEARCH_NOTE)

# The Writing discussion's decision turn. Same research rule, none of the Chat
# tab's paragraphs about staging, asking or remembering — it has no confirm
# cards to stage onto and no standing record of the user to write into.
RESEARCH_TURN_NOTE = f"""Right now your only job is to decide whether this \
message needs you to look something up — your reply comes afterwards, in a \
separate turn.

{research_tools.RESEARCH_NOTE}

If the message needs none of this, write nothing at all: anything you type in \
this turn is discarded."""

# Appended to the research turn only when a project scope came with the
# request. The second paragraph is the one that changes behaviour: the context
# panel means the model's working assumption is "what I was given is what
# exists", and the tools are worthless until that is explicitly broken.
WRITING_RECALL_NOTE = """This project has other chapters and notes that are \
not in front of you. writing_list shows what exists, writing_read opens one in \
full, and writing_search finds a name or a phrase across all of them.

Anything the author attached to this conversation is already above. Use these \
for what they did not attach — before asking them to paste something in, and \
before inventing a detail the story has already settled."""

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
    action_calls: set[tuple[str, str]] = set()
    # The research half is the shared one (backend/delegate/research_tools.py):
    # same three tools, same offline-first gate, same per-run state object the
    # Ideas and Writing discussions get.
    _tools, dispatch, _research = research_tools.build(
        checkpoint=checkpoint, deadline=deadline)

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

    dispatch.update({name: life_wiki for name in _LIFE_WIKI_NAMES})
    dispatch.update({name: life for name in life_tools.TOOL_NAMES})
    proposal_dispatch = SimpleNamespace(run_tool=run_proposal)
    dispatch.update({
        tool['function']['name']: proposal_dispatch for tool in proposal_tools.TOOLS
    })
    return dispatch


def _toolbox(toolset: str, *, conversation_id, checkpoint, deadline,
             writing_project_id=None):
    """(tools, dispatch, decision_note) for one run, or None for no tool turn.

    Three named toolsets rather than a boolean, because there are three
    genuinely different answers and only one of them is "all of it". A boolean
    could not express the middle one without the *caller* passing the tool
    list — and that caller is backend/routes/chat.py, which has no business
    knowing what is in the delegate's toolboxes.

    - 'chat'     — the Chat tab: research, proposals, recall, the life wiki.
    - 'research' — the Writing discussion: research and nothing else. No
                   confirm cards exist on that screen to accept a proposal on,
                   and the life wiki is about the user rather than the piece
                   they are writing.
    - 'none'     — the voice listener, task nudges, the morning check-in: they
                   speak their replies aloud, so a gathering turn is latency
                   before the first word with no UI to show a step in.

    A 'research' run also gets its project's chapters and notes when the
    request named a project — everything the author did not tick into the
    prompt. Each toolbox is constructed inside the branch that uses it:
    LifeTools built unconditionally would leave a Writing run one dispatch-map
    typo away from the user's journal, and the same holds in reverse.
    """
    if toolset == 'research':
        tools, dispatch, _state = research_tools.build(
            checkpoint=checkpoint, deadline=deadline)
        if writing_project_id:
            writing = writing_tools.WritingTools(writing_project_id)
            tools = tools + writing_tools.TOOLS
            dispatch.update({n: writing for n in writing_tools.TOOL_NAMES})
            return tools, dispatch, f'{RESEARCH_TURN_NOTE}\n\n{WRITING_RECALL_NOTE}'
        return tools, dispatch, RESEARCH_TURN_NOTE
    if toolset == 'chat':
        life = LifeTools(conversation_id)
        # Scoped to the life wiki, so a chat can never surface a note about a
        # codebase — and, in the other direction, the Ideas agent's default
        # WikiTools scope keeps the user's life out of a research turn.
        life_wiki = wiki_tools.WikiTools(kind=wiki_tools.LIFE_KIND)
        return TOOLS, _main_dispatch(
            life=life, life_wiki=life_wiki, checkpoint=checkpoint,
            deadline=deadline), DECISION_NOTE
    return None


def stream_reply(messages: list[dict], system_prompt: str = '', *,
                 toolset: str = 'chat', checkpoint=None,
                 conversation_id: str | None = None,
                 writing_project_id: str | None = None):
    """Yields ('step', event) as each tool call finishes, then ('thinking',
    delta) and ('content', delta) as the reply streams, then one
    ('done', {steps, sources, proposals, truncated}).

    Steps and proposals go out on the `done` payload as well as live, because
    the browser persists them onto the assistant message's metadata — a reload
    has to redraw the same trace, and the live events are gone by then.

    `toolset` names which tools this run gets: 'chat' (everything), 'research'
    (the offline-first research chain alone, for the Writing discussion) or
    'none', which skips the decision turn entirely. 'none' is for the callers
    with no UI to confirm anything in: the voice listener, task nudges and the
    morning check-in all speak their replies aloud, where a staged card is
    invisible and the decision turn is pure added latency before the user hears
    a word. See `_toolbox`.

    `writing_project_id` scopes the Writing discussion's recall to one project.
    It has to be named by the caller: a request carrying a `systemPrompt` takes
    the inline path, so there is no `conversationId` and the server never reads
    `conversations.writing_project_id`.

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

    evidence: list[dict] = []
    box = _toolbox(toolset, conversation_id=conversation_id,
                   checkpoint=checkpoint, deadline=deadline,
                   writing_project_id=writing_project_id)
    if box:
        tools, dispatch, decision_note = box
        gathered: dict = {}
        for kind, payload in tool_loop.gather_events(
                initial_messages=conversation + [
                    {'role': 'user', 'content': decision_note},
                ],
                tools=tools, dispatch=dispatch, checkpoint=checkpoint,
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
