"""Shared LLM helpers over llama.cpp's `llama-server`.

`llama-server` implements the OpenAI API, including the two things that used to
force this module onto Ollama's native endpoint — and both are now *better*:

- **Context window.** There is no per-request `num_ctx` to set, because the KV
  cache is allocated once when the model loads (`ctx-size` in
  `llama/presets.ini`). The whole "keep one shared num_ctx or the model reloads"
  problem simply doesn't exist here.
- **JSON.** Instead of Ollama's all-or-nothing `format: json`, llama-server
  compiles a JSON *schema* into a GBNF grammar, so a structured call is valid by
  construction rather than parsed out of prose. And unlike Ollama's grammar mode,
  it composes with thinking: the grammar applies to the answer channel only.

Thinking is a boolean, not a level: the chat model has one thinking channel,
toggled by a chat-template kwarg — there is no low/medium/high/max to map onto.
That was true of Gemma 4 and stays true of Qwen3.6, which is why the swap needed
no change here.
"""
import json
import logging
import re

from backend.ai import service
from backend.ai.provider import get_llama_client, get_model, get_provider_config

logger = logging.getLogger(__name__)


class ToolCallingUnsupported(Exception):
    """Raised when the active provider cannot drive an OpenAI-style tool loop."""


class EmptyCompletion(ValueError):
    """Raised when a JSON-mode completion comes back with no usable content."""


# Ceiling for JSON-mode completions. Without a cap a model that falls into a
# degenerate repetition loop keeps emitting until the context fills; this keeps
# the call bounded while staying far above any real briefing/flashcard payload.
JSON_MAX_TOKENS = 4096

# Output length ceiling (`max_tokens`) for the default conversational model. A
# hard stop, not a reservation, so it costs nothing until a generation runs long —
# but it has to stay reachable within `_TIMEOUT`. The chat model serves its experts
# from system RAM and measures tens of tok/s, so a ceiling in the tens of thousands
# of tokens could not finish, turning a runaway generation into a lost reply
# rather than a capped one.
LLM_MAX_TOKENS = 4096

# How long to wait on a blocking generation. Deliberately roomy: the model is
# large and mostly CPU-resident, and slower still when thinking.
_TIMEOUT = 1800


def default_generation_opts() -> dict:
    """Thinking + output ceiling for the default (conversational) model, read from
    user settings. The context window is deliberately absent — it belongs to the
    server, not the request. Structured `chat_json` calls manage both themselves.
    """
    from backend.ai.provider import get_settings
    s = get_settings() or {}
    return {
        'thinking': bool(s.get('llm_thinking')),
        'max_tokens': s.get('llm_max_tokens') or LLM_MAX_TOKENS,
    }


_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$', re.IGNORECASE)
_THINK_RE = re.compile(r'<think>.*?</think>', re.IGNORECASE | re.DOTALL)


def _parse_json_response(content: str | None) -> dict:
    """Best-effort parse of a JSON completion.

    With a schema attached llama-server guarantees well-formed JSON, so this is
    now a fallback rather than the main path — it still matters for the
    schema-less calls and for thinking models that leak a <think> block or wrap
    the object in a ```json fence, which makes a bare `json.loads` blow up with an
    opaque "Expecting value" error.
    """
    text = _THINK_RE.sub('', content or '').strip()
    if not text:
        raise EmptyCompletion('model returned empty content for a JSON request')
    stripped = _FENCE_RE.sub('', text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise EmptyCompletion(f'model returned non-JSON content: {text[:200]!r}')


def _messages(prompt: str, system: str | None = None) -> list[dict]:
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})
    return messages


def _request_kwargs(*, thinking: bool, max_tokens: int | None,
                    schema: dict | None = None) -> dict:
    """The non-message half of a chat request.

    `chat_template_kwargs` rides in `extra_body` because it is a llama.cpp
    extension the OpenAI SDK doesn't model. Thinking is disabled explicitly
    rather than by omission, because the chat template defaults it *on* — that
    was true of Gemma 4 and is true of Qwen3.6. Being explicit is also what
    makes the setting safe across a model swap: a template that doesn't know
    the kwarg ignores it, so the worst case is the default, never a crash.
    """
    kwargs: dict = {
        'extra_body': {'chat_template_kwargs': {'enable_thinking': thinking}},
    }
    if max_tokens:
        kwargs['max_tokens'] = max_tokens
    if schema is not None:
        # No `strict` flag: that is an OpenAI-ism whose subset rules would force
        # every property to be required. llama.cpp converts the schema to GBNF
        # directly and honours optional properties, which several of these call
        # sites rely on.
        kwargs['response_format'] = {
            'type': 'json_schema',
            'json_schema': {'name': 'response', 'schema': schema},
        }
    return kwargs


def _content(message) -> str:
    """Assistant text, ignoring the thinking channel.

    Depending on how it was launched, llama-server either splits reasoning into a
    `reasoning_content` field or leaves it inline in `content`; we want the answer
    either way, so read `content` and let `_parse_json_response` strip any inline
    <think> block that slipped through.
    """
    return getattr(message, 'content', None) or ''


# --------------------------------------------------------------------------
# Every request below is issued as a *stream*, even the blocking ones.
#
# That is not about latency — it is the only way a call can be abandoned. A
# blocking `create()` sits inside the SDK until the whole generation is done,
# with no way for another thread to interrupt it, so a background call could
# not be preempted for an interactive one however much the broker wanted to.
# Streaming lets us stop iterating and close the response, which llama-server
# sees as a client disconnect and answers by cancelling the task and freeing
# the slot (backend/ai/service.py has the reference).
#
# The blocking helpers reassemble the deltas and return the same shapes they
# always did, so no caller can tell the difference.
# --------------------------------------------------------------------------

class _ToolFunction:
    __slots__ = ('name', 'arguments')

    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    __slots__ = ('id', 'type', 'function')

    def __init__(self, id: str, function: _ToolFunction):
        self.id = id
        self.type = 'function'
        self.function = function


class _Message:
    """The subset of an OpenAI assistant message this app actually reads.

    Rebuilt from stream deltas rather than taken whole, so it has to mirror
    `serialize_tool_calls` (backend/ai/mcp_client.py) and the tool loop's
    `msg.content` / `msg.tool_calls` access exactly.
    """
    __slots__ = ('content', 'reasoning_content', 'tool_calls')

    def __init__(self, content, reasoning_content, tool_calls):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or None


def _iter_stream(stream, cancel):
    """Yield chunks, abandoning the request if `cancel` fires.

    Closing the stream is what actually frees the llama-server slot, so it
    happens in a `finally` — an exception on our side must not leave a
    generation running on the card with nobody reading it.
    """
    try:
        for chunk in stream:
            if cancel is not None and cancel.is_set():
                raise service.Preempted('cancelled for an interactive call')
            yield chunk
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _collect(stream, cancel) -> tuple[_Message, str | None]:
    """Drain a stream into one assistant message plus its finish reason."""
    content: list[str] = []
    reasoning: list[str] = []
    # Keyed by the delta's `index`, which is how the OpenAI wire format ties
    # argument fragments back to the call they belong to. Ordered dict, so the
    # calls come out in the order the model asked for them.
    calls: dict[int, dict] = {}
    finish_reason = None

    for chunk in _iter_stream(stream, cancel):
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        finish_reason = choice.finish_reason or finish_reason
        delta = choice.delta
        if delta is None:
            continue

        text = getattr(delta, 'content', None)
        if text:
            content.append(text)
        think = getattr(delta, 'reasoning_content', None)
        if think:
            reasoning.append(think)

        for position, tc in enumerate(getattr(delta, 'tool_calls', None) or []):
            index = getattr(tc, 'index', None)
            if index is None:
                index = position
            slot_ = calls.setdefault(index, {'id': None, 'name': None, 'arguments': ''})
            if getattr(tc, 'id', None):
                slot_['id'] = tc.id
            fn = getattr(tc, 'function', None)
            if fn is not None:
                if getattr(fn, 'name', None):
                    slot_['name'] = fn.name
                # Arguments arrive as a stream of JSON fragments; concatenating
                # them is the whole reassembly.
                if getattr(fn, 'arguments', None):
                    slot_['arguments'] += fn.arguments

    tool_calls = [
        _ToolCall(c['id'] or f'call_{i}', _ToolFunction(c['name'] or '', c['arguments']))
        for i, c in sorted(calls.items())
        if c['name']
    ]
    return (
        _Message(''.join(content), ''.join(reasoning) or None, tool_calls),
        finish_reason,
    )


def _complete(*, messages: list[dict], label: str, model: str | None = None,
              tools: list[dict] | None = None, timeout: float = _TIMEOUT,
              **request_kwargs) -> tuple[_Message, str | None]:
    """One blocking generation, issued as a stream and held under a lane slot.

    Every chat completion in the app comes through here — including the
    multimodal ones in `images.py` and `audio_description.py`, which used to
    build their own requests and so were invisible to admission control.
    """
    c = get_provider_config()
    client = get_llama_client(c)
    alias = model or get_model(c)
    extra = {'tools': tools} if tools else {}
    with service.slot(lane=service.lane_for(alias), label=label) as cancel:
        stream = client.chat.completions.create(
            model=alias, messages=messages, stream=True, timeout=timeout,
            **extra, **request_kwargs,
        )
        return _collect(stream, cancel)


def chat_json(prompt: str, system: str | None = None, model: str | None = None,
              max_tokens: int = JSON_MAX_TOKENS, thinking: bool = False,
              schema: dict | None = None) -> dict:
    """Blocking JSON completion; returns the parsed object.

    Pass `schema` (a JSON Schema object) to have llama-server constrain the output
    to it via grammar — strongly preferred, since it removes the whole class of
    "the model wrote prose instead of JSON" failures. Without one the call still
    works and falls back to `_parse_json_response`.

    Thinking is off by default: for these structured calls it only adds latency,
    and the answer is machine-read rather than shown to the user.
    """
    message, _ = _complete(
        messages=_messages(prompt, system), model=model, label='chat_json',
        **_request_kwargs(thinking=thinking, max_tokens=max_tokens, schema=schema),
    )
    return _parse_json_response(_content(message))


def chat_text(prompt: str, system: str | None = None) -> str:
    """Blocking plain-text completion (default model's thinking/token settings)."""
    return chat_messages(_messages(prompt, system))


def chat_messages(messages: list[dict]) -> str:
    """Blocking plain-text completion over a prebuilt message list (default
    model's thinking/token settings)."""
    message, _ = _complete(
        messages=messages, label='chat_messages',
        **_request_kwargs(**default_generation_opts()),
    )
    return _content(message)


def chat_stream_deltas(messages: list[dict]):
    """Streaming plain-text completion; yields assistant content deltas.

    Thinking deltas are dropped here. Callers that want to show reasoning use
    `chat_stream_events` below and label the two channels apart — a caller that
    only wants the answer must not have to filter the thinking back out.
    """
    for kind, text in chat_stream_events(messages):
        if kind == 'content':
            yield text


_THINK_OPEN = '<think>'
_THINK_CLOSE = '</think>'
# Qwen's native tool-call notation. llama-server only turns this back into
# OpenAI `tool_calls` when the request carried `tools=`, and the answering turn
# deliberately carries none — so a model that decides to call something here
# emits the raw notation straight into `content`, where it renders as the reply.
_TOOL_OPEN = '<tool_call>'
_TOOL_CLOSE = '</tool_call>'

# Which markers end each state, and what state each leads to. 'content' and
# 'thinking' are also the channel names yielded for text in them; 'tool_call'
# has no channel, because that text is dropped.
_EXITS = {
    'content': ((_THINK_OPEN, 'thinking'), (_TOOL_OPEN, 'tool_call')),
    'thinking': ((_THINK_CLOSE, 'content'),),
    'tool_call': ((_TOOL_CLOSE, 'content'),),
}


def chat_stream_events(messages: list[dict]):
    """Streaming completion as ('content' | 'thinking', delta) pairs, plus one
    final ('truncated', True) if the model hit its output ceiling.

    Reasoning reaches us two different ways depending on how llama-server was
    launched: as a separate `reasoning_content` field on the delta, or inline in
    `content` wrapped in a <think> block. Both are handled, because which one
    you get is a property of the server's flags rather than of this request —
    and a UI that renders raw `<think>` tags into the reply is the failure this
    exists to prevent.

    The inline case is tracked with a running flag rather than a regex over the
    finished text: the tags arrive split across chunks, so there is no complete
    string to match against until the stream is over, by which point the answer
    should already be on screen.

    The `truncated` signal exists because a thinking model can spend the entire
    `max_tokens` budget inside one <think> block and stop before writing a word
    of answer — which arrives here as a perfectly successful stream carrying no
    content at all, indistinguishable from a model that had nothing to say.

    **Native tool-call notation is dropped, not shown.** llama-server rebuilds
    OpenAI `tool_calls` by running a grammar over the model's own notation, and
    it only does that when the request carried `tools=`. This one never does —
    tools belong to the blocking decision turn — so a model that decides to call
    something anyway emits `<tool_call><function=remember>…` as plain content,
    and it was rendering as the reply. Dropping it is the only honest option:
    nothing here can execute the call, and printing a request the app silently
    ignored is worse than printing nothing.
    """
    c = get_provider_config()
    client = get_llama_client(c)
    alias = get_model(c)
    buffer = ''
    state = 'content'
    dropped = False
    finish_reason = None
    # The slot is held for the generator's whole life, not just the request:
    # the caller is still reading tokens off it. The `with` unwinds on
    # GeneratorExit too, so an SSE client that disconnects mid-reply releases
    # the lane instead of holding it until the TTL sweep.
    with service.slot(lane=service.lane_for(alias), label='chat_stream') as cancel:
        stream = client.chat.completions.create(
            model=alias, messages=messages, stream=True, timeout=_TIMEOUT,
            **_request_kwargs(**default_generation_opts()),
        )
        for chunk in _iter_stream(stream, cancel):
            if not chunk.choices:
                continue
            finish_reason = chunk.choices[0].finish_reason or finish_reason
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                yield ('thinking', reasoning)

            text = getattr(delta, 'content', None)
            if not text:
                continue

            buffer += text
            # Hold back anything that could still turn out to be a partial tag,
            # so a '<' that begins '<think>' is never emitted as answer text.
            while buffer:
                exits = _EXITS[state]
                hit = None
                for marker, nxt in exits:
                    at = buffer.find(marker)
                    if at >= 0 and (hit is None or at < hit[0]):
                        hit = (at, marker, nxt)
                if hit:
                    at, marker, nxt = hit
                    head, buffer = buffer[:at], buffer[at + len(marker):]
                    if head and state != 'tool_call':
                        yield (state, head)
                    dropped = dropped or nxt == 'tool_call'
                    state = nxt
                    continue
                # Any of this state's markers could be the one starting here, so
                # the longest possible partial is what has to be held back.
                keep = max(_partial_tag_len(buffer, m) for m, _ in exits)
                emit, buffer = (buffer[:-keep], buffer[-keep:]) if keep else (buffer, '')
                if emit and state != 'tool_call':
                    yield (state, emit)
                break

    # An unclosed <tool_call> takes the rest of the reply with it, the same way
    # an unclosed <think> stays reasoning: half a call is no more printable than
    # a whole one.
    if buffer and state != 'tool_call':
        yield (state, buffer)
    if dropped:
        logger.warning('Dropped native tool-call notation from a streamed reply')
    if finish_reason == 'length':
        logger.warning('Reply hit the output ceiling (%s tokens)',
                       default_generation_opts()['max_tokens'])
        yield ('truncated', True)


def _partial_tag_len(buffer: str, marker: str) -> int:
    """How many trailing chars of `buffer` could be the start of `marker`."""
    for n in range(min(len(marker) - 1, len(buffer)), 0, -1):
        if buffer.endswith(marker[:n]):
            return n
    return 0


def chat_tool_turn(messages: list[dict], tools: list[dict], max_tokens: int | None = None):
    """One tool-calling turn as `(message, finish_reason)`.

    Callers that cap `max_tokens` need the finish_reason: a turn cut off at the
    ceiling comes back with no `tool_calls`, which is indistinguishable from the
    model deciding it is finished unless you look. `'length'` means truncated,
    `'stop'` means done.
    """
    return _complete(
        messages=messages, tools=tools, label='chat_tool_turn',
        **_request_kwargs(thinking=False, max_tokens=max_tokens),
    )


def chat_with_tools(messages: list[dict], tools: list[dict], max_tokens: int | None = None):
    """One tool-calling turn; returns the assistant message.

    llama-server parses the model's native tool-call notation into OpenAI-shaped
    `tool_calls`. It picks the parser by reading the model's own chat template,
    so this works across model families without the app knowing which notation
    is in play — but only when the server runs with `--jinja` (`jinja = true` in
    llama/presets.ini, set globally for exactly this reason).

    `max_tokens` defaults to unbounded, as it always has — verification wants a
    whole case. Background loops should still pass a small ceiling. A generation
    *is* now preemptible (backend/ai/service.py cancels it mid-stream for an
    interactive call), but preemption throws the turn away and re-runs it, so a
    short turn is the difference between losing seconds and losing minutes.
    Anything that caps it should call `chat_tool_turn` instead and check why the
    turn ended.
    """
    return chat_tool_turn(messages, tools, max_tokens)[0]
