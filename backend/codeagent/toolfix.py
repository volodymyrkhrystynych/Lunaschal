"""Rescue a tool call the model wrote inside its own thinking block.

llama-server builds OpenAI `tool_calls` by parsing the model's native notation,
and it splits the output at `</think>` first: anything before that is reasoning.
Qwen 3.5/3.6 sometimes opens a `<tool_call>` *before* closing its reasoning
(llama.cpp #20837, #22684), so the call lands in `reasoning_content`, the
response carries no `tool_calls`, and the turn ends with `finish_reason=stop`.
The coding agent reads that as "the model is finished" — the call it can see on
screen never runs. Turning thinking off would prevent it, and is what the app's
own tool loop does (`backend/ai/llm.py`'s `chat_tool_turn`), but Qwen needs to
think for anything past a one-line change, so the proxy repairs it instead.

`check` is pure: a request and a buffered response in, a verdict out.

- `ok`      — nothing to do.
- `promote` — a complete, valid call is sitting at the end of the reasoning;
              `verdict.response` is the response rewritten with it as a real
              `tool_calls` entry. No extra generation.
- `poke`    — there is a call in there, but it is cut off, names a tool the
              request did not offer, or has arguments that do not fit the
              schema. The proxy regenerates with `poke_messages`.

Two things are deliberately *not* rescued. A reply with visible content is an
answer, whatever the reasoning mused about. And `finish_reason=length` is a
truncation, which is a different problem with a different fix (a bigger
`max_tokens`), so it is left for the agent to see.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
from dataclasses import dataclass, field

TOOL_OPEN = '<tool_call>'
TOOL_CLOSE = '</tool_call>'
_THINK_OPEN = '<think>'
_THINK_CLOSE = '</think>'

_BLOCK = re.compile(r'<tool_call>(.*?)</tool_call>', re.S)
_FUNCTION = re.compile(r'<function=([^>\s]+)>(.*?)</function>', re.S)
_PARAM = re.compile(r'<parameter=([^>\s]+)>(.*?)</parameter>', re.S)

# How much trailing text may follow the last call and still count as "the call
# was the last thing the model did". A model that wrote a call and then kept
# reasoning for a paragraph was thinking *about* a call, not making one.
TRAILING_SLACK = 200

POKE_TEXT = (
    'The tool call above was written inside your thinking block, so it was '
    'never executed. Close your reasoning and emit the tool call again as a '
    'real tool call. Do not repeat your reasoning.'
)


@dataclass
class Verdict:
    action: str                      # 'ok' | 'promote' | 'poke'
    reason: str = ''
    response: dict | None = None     # set for 'promote'
    calls: list[dict] = field(default_factory=list)
    attempted: str = ''              # the raw notation found, for 'poke'


def split_reasoning(message: dict) -> tuple[str, str]:
    """(reasoning, visible content) for a buffered assistant message.

    Reasoning arrives either as `reasoning_content` or inline as a <think>
    block in `content`, depending on llama-server's flags — both are handled,
    as `chat_stream_events` does. An unclosed <think> is all reasoning. A bare
    `</think>` with no opener is the common inline shape for Qwen, whose chat
    template puts the opening tag in the prompt rather than the output.
    """
    reasoning = message.get('reasoning_content') or ''
    content = message.get('content') or ''
    if _THINK_CLOSE in content and _THINK_OPEN not in content:
        inner, _, after = content.partition(_THINK_CLOSE)
        return (reasoning + inner) if reasoning else inner, after
    if _THINK_OPEN in content:
        before, _, rest = content.partition(_THINK_OPEN)
        inner, closed, after = rest.partition(_THINK_CLOSE)
        reasoning = (reasoning + inner) if reasoning else inner
        content = before + after if closed else before
    return reasoning, content


def _offered_tools(request: dict) -> dict[str, dict]:
    out = {}
    for tool in request.get('tools') or []:
        fn = (tool or {}).get('function') or {}
        if fn.get('name'):
            out[fn['name']] = fn.get('parameters') or {}
    return out


def _coerce(value: str, schema: dict):
    """A Qwen <parameter> value is always text; the schema says what it means."""
    kind = (schema or {}).get('type')
    text = value.strip('\n')
    if kind in (None, 'string'):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise ValueError(f'expected {kind}, got {text[:40]!r}')


_TYPES = {
    'string': str, 'integer': int, 'number': (int, float), 'boolean': bool,
    'array': list, 'object': dict, 'null': type(None),
}


def _fits(args: dict, schema: dict) -> str | None:
    """A shallow schema check — enough to refuse a call the agent would reject.
    Returns the problem, or None."""
    props = (schema or {}).get('properties') or {}
    for name in (schema or {}).get('required') or []:
        if name not in args:
            return f'missing required argument {name!r}'
    if (schema or {}).get('additionalProperties') is False:
        extra = sorted(set(args) - set(props))
        if extra:
            return f'unexpected argument {extra[0]!r}'
    for name, value in args.items():
        kind = (props.get(name) or {}).get('type')
        expected = _TYPES.get(kind) if isinstance(kind, str) else None
        if expected is None:
            continue
        if kind in ('integer', 'number') and isinstance(value, bool):
            return f'argument {name!r} should be {kind}'
        if not isinstance(value, expected):
            return f'argument {name!r} should be {kind}'
    return None


def _parse_block(body: str, tools: dict[str, dict]) -> tuple[dict | None, str]:
    """One <tool_call> body -> ({name, arguments}, '') or (None, why not).

    Two notations: Qwen3-Coder's XML (`<function=x><parameter=y>…`), which is
    what Qwen3.6 emits, and the older JSON object form.
    """
    body = body.strip()
    fn = _FUNCTION.search(body)
    if fn:
        name = fn.group(1).strip()
        if name not in tools:
            return None, f'tool {name!r} was not offered'
        props = (tools[name].get('properties') or {})
        args = {}
        try:
            for p in _PARAM.finditer(fn.group(2)):
                key = p.group(1).strip()
                args[key] = _coerce(p.group(2), props.get(key) or {})
        except ValueError as e:
            return None, str(e)
    else:
        try:
            obj = json.loads(body)
        except ValueError:
            return None, 'tool call is neither XML nor JSON'
        if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
            return None, 'tool call has no name'
        name = obj['name']
        if name not in tools:
            return None, f'tool {name!r} was not offered'
        args = obj.get('arguments') or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return None, 'arguments are not JSON'
        if not isinstance(args, dict):
            return None, 'arguments are not an object'
    problem = _fits(args, tools[name])
    if problem:
        return None, problem
    return {'name': name, 'arguments': args}, ''


def _trailing_blocks(reasoning: str) -> tuple[list[str], str]:
    """The run of <tool_call> blocks the reasoning *ends* with.

    Returns (bodies, problem). An unclosed final <tool_call> is a problem — the
    model was cut off or wandered off mid-call — rather than nothing.
    """
    last_open = reasoning.rfind(TOOL_OPEN)
    if last_open < 0:
        return [], ''
    if reasoning.find(TOOL_CLOSE, last_open) < 0:
        return [], 'the tool call is not closed'
    matches = list(_BLOCK.finditer(reasoning))
    tail = reasoning[matches[-1].end():]
    if len(tail.strip()) > TRAILING_SLACK:
        # Reasoning carried on after the call: it was discussed, not made.
        return [], 'reasoning continued after the call'
    bodies = [matches[-1].group(1)]
    for prev, nxt in zip(reversed(matches[:-1]), reversed(matches[1:])):
        if reasoning[prev.end():nxt.start()].strip():
            break
        bodies.insert(0, prev.group(1))
    return bodies, ''


def check(request: dict, response: dict) -> Verdict:
    tools = _offered_tools(request)
    if not tools:
        return Verdict('ok', 'request offered no tools')
    choices = response.get('choices') or []
    if not choices:
        return Verdict('ok', 'no choices')
    choice = choices[0]
    message = choice.get('message') or {}
    if message.get('tool_calls'):
        return Verdict('ok', 'tool calls already parsed')
    if choice.get('finish_reason') not in (None, 'stop'):
        return Verdict('ok', f"finish_reason={choice.get('finish_reason')}")
    reasoning, content = split_reasoning(message)
    if TOOL_OPEN not in reasoning:
        return Verdict('ok', 'no call in the reasoning')
    if content.strip():
        return Verdict('ok', 'the reply has visible content')

    bodies, problem = _trailing_blocks(reasoning)
    attempted = reasoning[reasoning.rfind(TOOL_OPEN):]
    if problem == 'reasoning continued after the call':
        return Verdict('ok', problem)
    if problem:
        return Verdict('poke', problem, attempted=attempted)

    calls = []
    for body in bodies:
        call, why = _parse_block(body, tools)
        if call is None:
            return Verdict('poke', why, attempted=attempted)
        calls.append(call)
    return Verdict('promote', f'{len(calls)} call(s) recovered from reasoning',
                   response=promote(response, calls), calls=calls)


def promote(response: dict, calls: list[dict]) -> dict:
    """The response with `calls` as real `tool_calls`, and the notation removed
    from the reasoning so the agent's transcript does not show it twice."""
    out = copy.deepcopy(response)
    choice = out['choices'][0]
    message = choice.setdefault('message', {})
    reasoning, _ = split_reasoning(message)
    cut = reasoning.find(TOOL_OPEN)
    message['reasoning_content'] = reasoning[:cut].rstrip() if cut >= 0 else reasoning
    message['content'] = None
    message['tool_calls'] = [{
        'id': f'call_{secrets.token_hex(8)}',
        'type': 'function',
        'function': {'name': c['name'], 'arguments': json.dumps(c['arguments'])},
    } for c in calls]
    choice['finish_reason'] = 'tool_calls'
    return out


def poke_messages(request: dict, verdict: Verdict) -> list[dict]:
    """The conversation to regenerate with: the original, the attempted call as
    the assistant's turn, and one sentence saying why nothing happened.

    The attempt goes in as visible content rather than as reasoning because
    Qwen's chat template drops reasoning from every turn before the last user
    message — which is exactly where this turn ends up.
    """
    return [
        *(request.get('messages') or []),
        {'role': 'assistant', 'content': verdict.attempted or TOOL_OPEN},
        {'role': 'user', 'content': POKE_TEXT},
    ]
