"""Streaming glue for the Chat tab: decide, act, then answer.

Replaces the post-hoc classifier. That one fired *after* the reply had already
been written, on a hand-rolled `should_classify` heuristic, and turned every
exception into a fake low-confidence "conversation" result — so a request that
failed produced no reply, no error and no log line, and looked exactly like a
message the user had never meant as a request at all. Here the decision happens
before the reply, the model makes it, and a failure is a visible error on the
stream.

**The decision turn is separate from the answer, and its prose is discarded.**
llama-server rebuilds OpenAI `tool_calls` by running a grammar over the model's
native call notation, and reassembling partial tool-call deltas across
chunks is how an argument goes missing in production — so the turn that may
carry a tool call cannot be streamed. It is capped hard (`DECISION_MAX_TOKENS`)
and told to emit nothing when nothing needs doing, which keeps the cost of the
common case to a couple of tokens rather than a whole discarded reply. The
answer then streams in its own turn, off a prompt llama-server has already
cached, so the restart costs generation and not prefill.

**The decision turn is one round, not a loop.** Proposals are one-shot, and the
delegate is itself a loop, so a second blocking turn would only double the dead
air before the first token.

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

from backend.ai.chat import (
    TIME_PREFIX_NOTE, build_chat_system_prompt, format_now_context, stamp_messages,
)
from backend.ai.llm import chat_stream_events, chat_tool_turn
from backend.ai.mcp_client import serialize_tool_calls
from backend.chat.context import expand_attachments
from backend.delegate import agent, limits, tools as proposal_tools

logger = logging.getLogger(__name__)


def _out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline

# Raised from 160 when the propose_* tools moved onto this turn: a delegate call
# is one string, but a calendar event with a description and tags is several
# times that, and a tool call truncated at the ceiling comes back with no
# `tool_calls` at all — silently indistinguishable from the model deciding it
# had nothing to do. Still a hard cap, because its job is to stop a model that
# decided *not* to act from writing a whole reply we are about to throw away.
#
# 320 -> 512 when propose_food_log joined: dish, place, notes, calories, rating
# and tags in one call is the largest argument set on this turn, and a food
# message often stages that *and* a `remember` for a name in the same breath.
#
# 512 -> 768 when propose_recipe joined: its `content` is a whole markdown
# recipe (ingredients + numbered instructions), the largest single string any
# tool here writes — larger than propose_food_log's notes field alone.
DECISION_MAX_TOKENS = 768

DELEGATE_TOOL = {
    'type': 'function',
    'function': {
        'name': 'delegate',
        'description': (
            'Hand a lookup to a research delegate that can search and read the '
            'web — quickly for a single fact, or with real depth for a broad '
            'question. Call it when answering needs current or specific '
            'information you are not confident about. Do not call it for '
            'ordinary conversation, for anything you can answer confidently '
            'yourself, or for recording something — the propose_ tools do that '
            'directly.'
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
            },
            'required': ['task'],
        },
    },
}

TOOLS = [DELEGATE_TOOL] + proposal_tools.TOOLS

DECISION_NOTE = """Right now your only job is to decide which of your tools this \
message needs, if any — your reply comes afterwards, in a separate turn.

Use the propose_ tools to record something the user asked you to record, and \
fill in every detail they actually gave: the deadline, the time, how important \
it is. Do not drop a detail because it was not the main point of the sentence.

Where they clearly meant a detail but were too vague for you to act on it — \
"soon", "before the trip", an event with no date you can work out — call \
ask_user instead of guessing at it. Where they implied nothing, do not ask: \
stage what they said and leave the rest empty.

Use delegate when answering needs something looked up on the web.

If the message needs none of this, write nothing at all: anything you type in \
this turn is discarded."""

ANSWER_INSTRUCTION = (
    'Reply to the user now, in your own voice. If the delegate found '
    'information, use it to actually answer the question — state the facts it '
    'found, in full, do not just gesture at having looked something up. If you '
    'staged anything, mention that in passing — a confirmation card is already '
    'showing, so do not repeat its contents back and do not say it is saved, '
    'because they still have to confirm it. If you asked for clarification, put '
    'that question to them plainly and do not pretend you staged the thing it '
    'is about. If something could not be done, say so plainly rather than '
    'glossing over it.\n\n'
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


def _decision_calls(messages: list[dict]) -> list:
    """The decision turn. Returns every tool call it made, in order.

    Never raises: a decision turn that fails should cost the tools, not the
    reply. The classifier's sin was swallowing failure *silently* — this logs it
    and carries on to an ordinary answer, which is a real degradation the user
    can still act in.

    All of the calls, not just the first: a message can legitimately stage a
    to-do and ask about a second one, and the old code kept only the first
    `delegate` call and dropped everything else on the turn.
    """
    try:
        # Appended as a user turn, not a system one: the same shape the Ideas
        # discussion uses for its answer instruction, and the one a chat
        # template actually attends to at the end of a conversation.
        msg, finish_reason = chat_tool_turn(
            messages + [{'role': 'user', 'content': DECISION_NOTE}],
            TOOLS,
            max_tokens=DECISION_MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Decision turn failed, answering without tools: %s', e)
        return []

    calls = [c for c in (getattr(msg, 'tool_calls', None) or [])
             if c.function.name in _KNOWN_TOOLS]
    if finish_reason == 'length' and not calls:
        # A turn cut off at the ceiling returns no tool_calls, which is
        # otherwise indistinguishable from the model deciding it had nothing to
        # do. Worth a line in the log, since it means a request went unactioned.
        logger.warning('Decision turn hit the %d-token ceiling with no tool call',
                       DECISION_MAX_TOKENS)
    return calls


def _args_of(call) -> dict:
    try:
        args = json.loads(call.function.arguments or '{}')
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


_KNOWN_TOOLS = {t['function']['name'] for t in TOOLS}


def stream_reply(messages: list[dict], system_prompt: str = '', *,
                 tools_enabled: bool = True, checkpoint=None):
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

    The chat deadline is established here rather than by the caller, so every
    caller gets it — the voice listener waiting on a spoken reply has more
    reason to want one than the Chat tab does, not less. It is the outer bound
    the delegate's own budget and its nested deep pass are both clamped to.
    """
    deadline = limits.chat_deadline()
    timed_out = False
    system = _system_prompt(messages, system_prompt)
    # Photos become text before stamping, not after: stamp_messages flattens a
    # message to `[today 21:58] <content>`, so anything that must reach the model
    # has to already be in `content` by then.
    conversation = [{'role': 'system', 'content': system}] + stamp_messages(
        expand_attachments(messages)
    )

    steps: list[dict] = []
    sources: list[dict] = []
    proposals: list[dict] = []

    calls = _decision_calls(conversation) if tools_enabled else []
    results: list[tuple[object, str]] = []

    for call in calls:
        if call.function.name == 'delegate':
            task = (_args_of(call).get('task') or '').strip()
            result: dict = {}
            for kind, payload in agent.run_events(task, checkpoint=checkpoint,
                                                  deadline=deadline):
                if kind == 'step':
                    steps.append(payload)
                    yield ('step', payload)
                else:
                    result = payload
            sources += result.get('sources', [])
            # Only the delegate's summary crosses over, never its whole
            # transcript: that compression is the point of delegating, and it
            # is what keeps this conversation's context flat as it grows.
            results.append((call, result.get('summary', '')))
        else:
            text, event = proposal_tools.run_tool(call.function.name, _args_of(call))
            steps.append(event)
            yield ('step', event)
            # ask_user's event deliberately carries no `proposal`, so asking can
            # never put a card built on a guess in front of the user.
            if event.get('proposal'):
                proposals.append(event['proposal'])
            results.append((call, text))

    if results:
        # The transcript keeps the *shape* of a tool exchange — one assistant
        # message carrying every call, then one tool message per call — so the
        # answering turn sees a well-formed history.
        conversation.append({
            'role': 'assistant',
            'content': None,
            'tool_calls': serialize_tool_calls([c for c, _ in results]),
        })
        for call, text in results:
            conversation.append({
                'role': 'tool',
                'tool_call_id': call.id,
                'content': text,
            })

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

    yield ('done', {'steps': steps, 'sources': sources, 'proposals': proposals,
                    'truncated': truncated, 'timedOut': timed_out})
