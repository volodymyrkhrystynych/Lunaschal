"""Shared Ollama LLM helpers.

These talk to Ollama's **native** `/api/chat` endpoint (over `requests`) rather
than the OpenAI-compat `/v1` shim, because only the native API lets us set
`num_ctx` (the context window) per request — and a too-small window is what makes
a reasoning model's thinking crowd out its actual answer. Tool calling
(`chat_with_tools`) still uses the OpenAI-compat client; it doesn't need these
knobs and its message shape is consumed elsewhere.
"""
import json
import re

import requests

from backend.ai.provider import get_provider_config, get_ollama_client, DEFAULT_MODELS


class ToolCallingUnsupported(Exception):
    """Raised when the active provider cannot drive an OpenAI-style tool loop."""


class EmptyCompletion(ValueError):
    """Raised when a JSON-mode completion comes back with no usable content."""


# Ceiling for JSON-mode completions. Without a cap a model that falls into a
# degenerate repetition loop keeps emitting until the context fills; this keeps
# the call bounded while staying far above any real briefing/flashcard payload.
JSON_MAX_TOKENS = 4096

# Reasoning levels accepted by Ollama (mapped to the native `think` field).
# 'none' disables thinking; the rest enable it at that level. Validate before
# sending so a bad value can't reach the model.
REASONING_EFFORTS = ('none', 'low', 'medium', 'high', 'max')

# Fallbacks for the default conversational model when the user hasn't set them.
# num_predict is a hard stop, not a reservation, so it costs nothing until a
# generation actually runs long — but it has to stay reachable within
# _NATIVE_TIMEOUT. On a large MoE served mostly from system RAM (~10-15 tok/s),
# anything past a few thousand tokens can't finish before the request times out,
# which turns a runaway generation into a lost reply rather than a capped one.
LLM_MAX_TOKENS = 4096   # num_predict — output length ceiling
# num_ctx, unlike num_predict, is allocated up front: Ollama sizes the KV cache
# for the full window at load time. Ollama's own default is 4096; 8192 leaves a
# thinking model room for prompt + reasoning + answer without reserving VRAM the
# conversation will never use.
LLM_NUM_CTX = 8192

# How long to wait on a blocking generation. The briefing model can be large and
# slow, especially when thinking, so this is deliberately roomy.
_NATIVE_TIMEOUT = 1800

# Keep the model loaded in memory for a while after each request. Ollama's
# default is 5m; large reasoning models can take much longer to load, so this
# reduces the chance that a tabbed-away chat has to wait for a full reload.
_KEEP_ALIVE = '14h'


def default_num_ctx() -> int:
    """The one context window every request shares.

    Ollama keys a loaded runner on its context size: a request whose `num_ctx`
    differs from the running one evicts it and reloads the weights from scratch.
    On a large local model that is tens of seconds of dead time per call — and a
    single chat message fans out into several calls (classify, then the reply),
    so a mismatch costs *two* reloads before the user sees a token.

    So there is deliberately one setting, applied to every call: chat, briefing,
    and every structured `chat_json` helper. What those calls may differ on is
    output length and reasoning level, neither of which touches the runner.

    Falls back to the module default when settings are unreachable (background
    threads, tests) rather than letting a helper silently send no `num_ctx` and
    inherit Ollama's own 4096.
    """
    try:
        from backend.ai.provider import get_settings
        s = get_settings() or {}
    except Exception:
        return LLM_NUM_CTX
    return s.get('llm_num_ctx') or LLM_NUM_CTX


def default_generation_opts() -> dict:
    """Reasoning level + context window + output ceiling for the default
    (conversational) model, read from user settings. One standard set applied the
    same way to every model, thinking or not. Structured `chat_json` calls manage
    reasoning and output length themselves (reasoning must default to 'none', or
    the JSON gets crowded out) — but they share `default_num_ctx()`."""
    from backend.ai.provider import get_settings
    s = get_settings() or {}
    effort = s.get('llm_reasoning_effort') or 'none'
    if effort not in REASONING_EFFORTS:
        effort = 'none'
    return {
        'reasoning_effort': effort,
        'num_ctx': default_num_ctx(),
        'num_predict': s.get('llm_max_tokens') or LLM_MAX_TOKENS,
    }


_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$', re.IGNORECASE)
_THINK_RE = re.compile(r'<think>.*?</think>', re.IGNORECASE | re.DOTALL)


def _parse_json_response(content: str | None) -> dict:
    """Best-effort parse of a JSON completion.

    Some models (reasoning models especially) return empty content or wrap the
    object in a ```json fence, which makes a bare `json.loads` blow up with an
    opaque "Expecting value" error. Coalesce None, strip fences/whitespace, and
    fall back to extracting the first {...} object before giving up with a clear
    error that says *why*.
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


def _native_url() -> str:
    c = get_provider_config()
    return c['ollama_url'].rstrip('/') + '/api/chat'


def _think_value(reasoning_effort: str):
    """Map our reasoning level to Ollama's native `think` field: False disables
    thinking (safe even on non-reasoning models), a level string enables it."""
    return False if reasoning_effort == 'none' else reasoning_effort


def _native_body(messages, *, model, reasoning_effort, num_ctx, num_predict,
                 json_format=False, stream=False) -> dict:
    body = {
        'model': model,
        'messages': messages,
        'stream': stream,
        'think': _think_value(reasoning_effort),
        'keep_alive': _KEEP_ALIVE,
    }
    # JSON grammar mode and thinking don't mix — a reasoning model answers inside
    # its thinking channel and satisfies the grammar with an empty `{}`. Only
    # constrain to JSON when we're NOT thinking; otherwise rely on the prompt plus
    # `_parse_json_response`.
    if json_format and reasoning_effort == 'none':
        body['format'] = 'json'
    options = {}
    if num_ctx:
        options['num_ctx'] = num_ctx
    if num_predict:
        options['num_predict'] = num_predict
    if options:
        body['options'] = options
    return body


def _native_chat(messages, *, model, reasoning_effort='none', num_ctx=None,
                 num_predict=None, json_format=False) -> str:
    """Blocking native `/api/chat`; returns the assistant message content."""
    body = _native_body(messages, model=model, reasoning_effort=reasoning_effort,
                        num_ctx=num_ctx, num_predict=num_predict,
                        json_format=json_format, stream=False)
    r = requests.post(_native_url(), json=body, timeout=_NATIVE_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get('error'):
        raise EmptyCompletion(f"ollama error: {data['error']}")
    return (data.get('message') or {}).get('content') or ''


def _native_chat_stream(messages, *, model, reasoning_effort='none', num_ctx=None,
                        num_predict=None):
    """Streaming native `/api/chat`; yields assistant content deltas (thinking,
    which arrives in a separate `thinking` field, is intentionally not yielded)."""
    body = _native_body(messages, model=model, reasoning_effort=reasoning_effort,
                        num_ctx=num_ctx, num_predict=num_predict, stream=True)
    with requests.post(_native_url(), json=body, stream=True,
                       timeout=_NATIVE_TIMEOUT) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            obj = json.loads(line)
            if obj.get('error'):
                raise EmptyCompletion(f"ollama error: {obj['error']}")
            delta = (obj.get('message') or {}).get('content')
            if delta:
                yield delta


def chat_json(prompt: str, system: str | None = None, model: str | None = None,
              max_tokens: int = JSON_MAX_TOKENS, reasoning_effort: str = 'none',
              num_ctx: int | None = None) -> dict:
    """Blocking JSON completion; returns the parsed object. Pass `model` to
    override the default chat model.

    `num_ctx` defaults to the shared `default_num_ctx()` and should almost never
    be overridden — a value that differs from the chat window makes Ollama reload
    the model (see `default_num_ctx`), which for these short structured calls
    costs far more than the call itself.

    Reasoning is off by default (`reasoning_effort='none'`), and only then is the
    response constrained to JSON grammar. Pass 'low'/'medium'/'high'/'max' for
    graded reasoning — the grammar constraint is dropped and the JSON is recovered
    from the prose the model writes. Thinking needs room inside the shared window:
    a reasoning model's thinking can otherwise fill it before it writes the answer,
    yielding empty content."""
    if reasoning_effort not in REASONING_EFFORTS:
        reasoning_effort = 'none'
    c = get_provider_config()
    model = model or c['ollama_model'] or DEFAULT_MODELS['ollama']
    content = _native_chat(
        _messages(prompt, system), model=model, reasoning_effort=reasoning_effort,
        num_ctx=num_ctx or default_num_ctx(), num_predict=max_tokens,
        json_format=True,
    )
    return _parse_json_response(content)


def chat_text(prompt: str, system: str | None = None) -> str:
    """Blocking plain-text completion (default model's reasoning/ctx/token settings)."""
    c = get_provider_config()
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    return _native_chat(_messages(prompt, system), model=model,
                        **default_generation_opts())


def chat_messages(messages: list[dict]) -> str:
    """Blocking plain-text completion over a prebuilt message list (default
    model's reasoning/ctx/token settings)."""
    c = get_provider_config()
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    return _native_chat(messages, model=model, **default_generation_opts())


def chat_with_tools(messages: list[dict], tools: list[dict]):
    """One tool-calling turn via the Ollama OpenAI-compat API; returns the message."""
    c = get_provider_config()
    client = get_ollama_client(c)
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    resp = client.chat.completions.create(model=model, messages=messages, tools=tools)
    return resp.choices[0].message
