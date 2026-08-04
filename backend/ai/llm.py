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

Thinking is a boolean, not a level. Gemma 4 has one thinking channel, toggled by
a chat-template kwarg — there is no low/medium/high/max to map onto.
"""
import json
import re

from backend.ai.provider import get_llama_client, get_model, get_provider_config


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
# but it has to stay reachable within `_TIMEOUT`. Gemma 4 26B A4B with its experts
# served from system RAM measures 25 tok/s, so a ceiling in the tens of thousands
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
    rather than by omission — Gemma 4's template defaults it *on*.
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
    c = get_provider_config()
    client = get_llama_client(c)
    resp = client.chat.completions.create(
        model=model or get_model(c),
        messages=_messages(prompt, system),
        timeout=_TIMEOUT,
        **_request_kwargs(thinking=thinking, max_tokens=max_tokens, schema=schema),
    )
    return _parse_json_response(_content(resp.choices[0].message))


def chat_text(prompt: str, system: str | None = None) -> str:
    """Blocking plain-text completion (default model's thinking/token settings)."""
    return chat_messages(_messages(prompt, system))


def chat_messages(messages: list[dict]) -> str:
    """Blocking plain-text completion over a prebuilt message list (default
    model's thinking/token settings)."""
    c = get_provider_config()
    client = get_llama_client(c)
    resp = client.chat.completions.create(
        model=get_model(c), messages=messages, timeout=_TIMEOUT,
        **_request_kwargs(**default_generation_opts()),
    )
    return _content(resp.choices[0].message)


def chat_stream_deltas(messages: list[dict]):
    """Streaming plain-text completion; yields assistant content deltas.

    Thinking deltas are intentionally not yielded — they arrive either as
    `reasoning_content` or inside a <think> block, and neither belongs in the
    user-visible stream.
    """
    c = get_provider_config()
    client = get_llama_client(c)
    stream = client.chat.completions.create(
        model=get_model(c), messages=messages, stream=True, timeout=_TIMEOUT,
        **_request_kwargs(**default_generation_opts()),
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, 'content', None)
        if text:
            yield text


def chat_tool_turn(messages: list[dict], tools: list[dict], max_tokens: int | None = None):
    """One tool-calling turn as `(message, finish_reason)`.

    Callers that cap `max_tokens` need the finish_reason: a turn cut off at the
    ceiling comes back with no `tool_calls`, which is indistinguishable from the
    model deciding it is finished unless you look. `'length'` means truncated,
    `'stop'` means done.
    """
    c = get_provider_config()
    client = get_llama_client(c)
    resp = client.chat.completions.create(
        model=get_model(c), messages=messages, tools=tools, timeout=_TIMEOUT,
        **_request_kwargs(thinking=False, max_tokens=max_tokens),
    )
    choice = resp.choices[0]
    return choice.message, choice.finish_reason


def chat_with_tools(messages: list[dict], tools: list[dict], max_tokens: int | None = None):
    """One tool-calling turn; returns the assistant message.

    llama-server parses Gemma 4's native `<|tool_call>call:NAME{...}` notation into
    OpenAI-shaped `tool_calls` via its peg-gemma4 grammar, which requires the
    server to run with `--jinja` (see llama/start-llama.sh).

    `max_tokens` defaults to unbounded, as it always has — verification wants a
    whole case. Background loops should pass a small ceiling: nothing preempts a
    generation once it starts, so the turn length *is* the granularity at which
    background work can yield to an interactive chat message (backend/ai/priority.py).
    Anything that caps it should call `chat_tool_turn` instead and check why the
    turn ended.
    """
    return chat_tool_turn(messages, tools, max_tokens)[0]
