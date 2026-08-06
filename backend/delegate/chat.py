"""Streaming glue for the Chat tab: decide, delegate, then answer.

Replaces the post-hoc classifier. That one fired *after* the reply had already
been written, on a hand-rolled `should_classify` heuristic, and turned every
exception into a fake low-confidence "conversation" result — so a request that
failed produced no reply, no error and no log line, and looked exactly like a
message the user had never meant as a request at all. Here the decision happens
before the reply, the model makes it, and a failure is a visible error on the
stream.

**The decision turn is separate from the answer, and its prose is discarded.**
llama-server rebuilds OpenAI `tool_calls` by running its peg-gemma4 grammar over
Gemma 4's native call notation, and reassembling partial tool-call deltas across
chunks is how an argument goes missing in production — so the turn that may
carry a tool call cannot be streamed. It is capped hard (`DECISION_MAX_TOKENS`)
and told to emit nothing when no hand-off is needed, which keeps the cost of the
common case to a couple of tokens rather than a whole discarded reply. The
answer then streams in its own turn, off a prompt llama-server has already
cached, so the restart costs generation and not prefill.
"""
import json
import logging

from backend.ai.chat import (
    TIME_PREFIX_NOTE, build_chat_system_prompt, format_now_context, stamp_messages,
)
from backend.ai.llm import chat_stream_events, chat_tool_turn
from backend.ai.mcp_client import serialize_tool_calls
from backend.delegate import agent

logger = logging.getLogger(__name__)

# A tool call plus its task string is short. The cap is what stops a model that
# decided *not* to delegate from writing a whole reply we are about to throw
# away — at ~25 tok/s, an uncapped decision turn would be dead air before the
# real answer even starts.
DECISION_MAX_TOKENS = 160

DELEGATE_TOOL = {
    'type': 'function',
    'function': {
        'name': 'delegate',
        'description': (
            'Hand a task to a delegate that can act: it stages to-dos, calendar '
            'events, calorie entries, notes to self and flashcards for the user '
            'to confirm, and it can search and read the web. Call it whenever '
            'the message asks for something to be done, recorded or looked up, '
            'rather than just talked about. Do not call it for ordinary '
            'conversation, or for anything you can answer confidently yourself.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'task': {
                    'type': 'string',
                    'description': (
                        'What needs doing, written out in full. The delegate '
                        'cannot see the conversation, so include every detail '
                        'it needs — what, when, how much.'
                    ),
                },
            },
            'required': ['task'],
        },
    },
}

DECISION_NOTE = """You have one tool: delegate. Right now, your only job is to \
decide whether this message needs it — your reply comes afterwards, in a \
separate turn.

Call delegate if the user asked for something to be done, recorded or looked up. \
Otherwise write nothing at all: anything you type in this turn is discarded."""

ANSWER_INSTRUCTION = (
    'Reply to the user now, in your own voice. If the delegate staged anything, '
    'mention it in passing — a confirmation card is already showing, so do not '
    'repeat its contents back and do not say it is saved, because they still '
    'have to confirm it. If the delegate could not do something, say so plainly '
    'rather than glossing over it.'
)


def _system_prompt(messages: list[dict], system_prompt: str = '') -> str:
    system = system_prompt or build_chat_system_prompt()
    system = f'{system}\n\n{format_now_context()}'
    if any(m.get('createdAt') for m in messages):
        system = f'{system}\n\n{TIME_PREFIX_NOTE}'
    return system


def _delegate_call(messages: list[dict]):
    """The decision turn. Returns the tool call to run, or None.

    Never raises: a decision turn that fails should cost the delegate, not the
    reply. The classifier's sin was swallowing failure *silently* — this logs it
    and carries on to an ordinary answer, which is a real degradation the user
    can still act in.
    """
    try:
        # Appended as a user turn, not a system one: the same shape the Ideas
        # discussion uses for its answer instruction, and the one Gemma 4's
        # template actually attends to at the end of a conversation.
        msg, _ = chat_tool_turn(
            messages + [{'role': 'user', 'content': DECISION_NOTE}],
            [DELEGATE_TOOL],
            max_tokens=DECISION_MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Delegate decision turn failed, answering without it: %s', e)
        return None

    for call in getattr(msg, 'tool_calls', None) or []:
        if call.function.name == 'delegate':
            return call
    return None


def _task_of(call) -> str:
    try:
        args = json.loads(call.function.arguments or '{}')
    except json.JSONDecodeError:
        args = {}
    return (args.get('task') or '').strip()


def stream_reply(messages: list[dict], system_prompt: str = '', *,
                 delegate: bool = True, checkpoint=None):
    """Yields ('step', event) while the delegate works, then ('thinking', delta)
    and ('content', delta) as the reply streams, then one
    ('done', {steps, sources, proposals}).

    Steps and proposals go out on the `done` payload as well as live, because
    the browser persists them onto the assistant message's metadata — a reload
    has to redraw the same trace, and the live events are gone by then.

    `delegate=False` skips the decision turn entirely, for the callers with no
    UI to confirm anything in: the voice listener, task nudges and the morning
    check-in all speak their replies aloud, where a staged card is invisible and
    the decision turn is pure added latency before the user hears a word.
    """
    system = _system_prompt(messages, system_prompt)
    conversation = [{'role': 'system', 'content': system}] + stamp_messages(messages)

    steps: list[dict] = []
    sources: list[dict] = []
    proposals: list[dict] = []

    call = _delegate_call(conversation) if delegate else None
    if call is not None:
        task = _task_of(call)
        result: dict = {}
        for kind, payload in agent.run_events(task, checkpoint=checkpoint):
            if kind == 'step':
                steps.append(payload)
                yield ('step', payload)
            else:
                result = payload
        sources = result.get('sources', [])
        proposals = result.get('proposals', [])

        # The transcript keeps the *shape* of a tool exchange — assistant call,
        # then tool result — so the answering turn sees a well-formed history.
        # Only the delegate's summary crosses over, never its whole transcript:
        # that compression is the point of delegating, and it is what keeps the
        # main chat's context flat as the toolbox grows.
        conversation.append({
            'role': 'assistant',
            'content': getattr(call, 'content', None),
            'tool_calls': serialize_tool_calls([call]),
        })
        conversation.append({
            'role': 'tool',
            'tool_call_id': call.id,
            'content': result.get('summary', ''),
        })

    conversation.append({'role': 'user', 'content': ANSWER_INSTRUCTION})
    for kind, delta in chat_stream_events(conversation):
        yield (kind, delta)

    yield ('done', {'steps': steps, 'sources': sources, 'proposals': proposals})
