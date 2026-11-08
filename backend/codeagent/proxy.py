"""The coding agent's model endpoint: OpenAI-compatible, on a Unix socket.

The agent in the container thinks it is talking to an ordinary
`http://127.0.0.1:8080/v1`. That is socat, forwarding to the socket this
module listens on, so every model call the agent makes passes through
`backend/ai/service.py` like any other call in the app — at background
priority, so a chat message takes the GPU lane back, and refused outright while
GPU inference is paused. An agent talking to llama-server directly would hold
half the card through every chat reply.

Three things happen here that the agent never sees:

- **Preemption becomes a slow reply.** A preempted call is abandoned upstream
  (closing the response makes llama-server free the slot), re-queued behind the
  interactive call, and sent again unchanged. Responses are therefore always
  *buffered*: a replay must not duplicate tokens the agent already received.
  While the call is in flight a streaming client gets SSE comment keepalives,
  then the finished response as a short synthesized stream.
- **A tool call written inside the thinking block is repaired** (toolfix.py):
  promoted to a real call when it is complete and valid, otherwise the model is
  poked to emit it again — at most POKE_LIMIT times — and given up on with a
  `stuck` event the phone hears about.
- **Every call is logged** to the task's event log with how long it waited for
  the lane and how long it ran, which are different answers to "why is this
  slow".

The socket path, not a token, is what identifies the task: one proxy per task,
listening on a socket that only that task's container has mounted.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

import requests
from flask import Flask, Response, jsonify, request

from backend.ai import service
from backend.ai.provider import get_model, get_provider_config
from backend.codeagent import toolfix

logger = logging.getLogger(__name__)

LABEL = 'code_agent'
# The name the agent's config uses for the model. Rewritten to the configured
# alias on the way through, so swapping the model never touches the image.
MODEL_NAME = 'local'

MIN_MAX_TOKENS = 32768     # at 8k, file writes were cut off and saved half-written
MAX_CANCELS = 3            # backend/ai/jobs.py's threshold, for the same reason
POKE_LIMIT = 2
KEEPALIVE_SECONDS = 10.0
PAUSE_WAIT_SECONDS = 600.0  # hold through a short pause; past this, 503
PAUSE_POLL_SECONDS = 5.0
UPSTREAM_TIMEOUT = (10, 1800)

# Qwen's published sampler for *precise coding* in thinking mode. The [qwen36]
# preset runs its general-thinking one (temperature 1.0) because that alias
# serves every chat_json call in the app; the agent asks for none, so without
# these it would write code at 1.0. Defaults only — a harness that sends its
# own value keeps it.
CODING_SAMPLER = {'temperature': 0.6, 'top_p': 0.95, 'top_k': 20,
                  'min_p': 0.0, 'presence_penalty': 0.0}

EventFn = Callable[[str, dict], None]


class UpstreamError(RuntimeError):
    pass


# ----------------------------------------------------------- pure helpers

def prepare(body: dict, alias: str) -> dict:
    """The request as it goes upstream: our alias, always streamed (so it can
    be cancelled), the coding sampler where the client set none, and a floor
    on max_tokens."""
    out = copy.deepcopy(body)
    for key, value in CODING_SAMPLER.items():
        out.setdefault(key, value)
    out['model'] = alias
    out['stream'] = True
    out.pop('stream_options', None)
    out['stream_options'] = {'include_usage': True}
    limit = out.get('max_tokens') or out.get('max_completion_tokens')
    out.pop('max_completion_tokens', None)
    out['max_tokens'] = max(int(limit or 0), MIN_MAX_TOKENS)
    return out


def assemble(chunks: Iterable[dict]) -> dict:
    """Fold OpenAI streaming chunks into one non-streaming chat.completion."""
    content, reasoning = [], []
    calls: dict[int, dict] = {}
    finish = None
    usage = None
    timings = None
    meta = {}
    for chunk in chunks:
        meta = meta or {k: chunk.get(k) for k in ('id', 'created') if chunk.get(k)}
        if chunk.get('usage'):
            usage = chunk['usage']
        if chunk.get('timings'):
            timings = chunk['timings']
        for choice in chunk.get('choices') or []:
            finish = choice.get('finish_reason') or finish
            delta = choice.get('delta') or {}
            if delta.get('content'):
                content.append(delta['content'])
            if delta.get('reasoning_content'):
                reasoning.append(delta['reasoning_content'])
            for pos, tc in enumerate(delta.get('tool_calls') or []):
                idx = tc.get('index', pos)
                slot = calls.setdefault(idx, {'id': None, 'name': '', 'arguments': ''})
                slot['id'] = tc.get('id') or slot['id']
                fn = tc.get('function') or {}
                slot['name'] = fn.get('name') or slot['name']
                slot['arguments'] += fn.get('arguments') or ''
    message = {'role': 'assistant', 'content': ''.join(content) or None}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if calls:
        message['tool_calls'] = [
            {'id': c['id'] or f'call_{i}', 'type': 'function',
             'function': {'name': c['name'], 'arguments': c['arguments']}}
            for i, c in sorted(calls.items()) if c['name']
        ]
    out = {
        'id': meta.get('id') or f'chatcmpl-{int(time.time() * 1000)}',
        'object': 'chat.completion',
        'created': meta.get('created') or int(time.time()),
        'model': MODEL_NAME,
        'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
    }
    if usage:
        out['usage'] = usage
    if timings:
        # llama-server's own numbers: prompt_n is what it had to prefill, cache_n
        # what it reused. Kept beside the completion, never sent to the agent.
        out['timings'] = timings
    return out


def cache_stats(completion: dict) -> dict:
    """How much of the prompt was prefilled and how much came from the cache.

    At 150k tokens a lost prompt cache is minutes of prefill on this hybrid
    model, and nothing else makes that visible: the call just looks slow."""
    t = completion.get('timings') or {}
    out = {}
    if isinstance(t.get('prompt_n'), int):
        out['prefilled'] = t['prompt_n']
    if isinstance(t.get('cache_n'), int):
        out['cached'] = t['cache_n']
    return out


def to_chunks(completion: dict) -> list[dict]:
    """A finished completion as a short OpenAI stream: reasoning, content, each
    tool call whole, then the finish. Clients reassemble any of these."""
    base = {'id': completion['id'], 'object': 'chat.completion.chunk',
            'created': completion['created'], 'model': completion['model']}
    choice = completion['choices'][0]
    msg = choice.get('message') or {}
    chunks = [{**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant'},
                                    'finish_reason': None}]}]
    if msg.get('reasoning_content'):
        chunks.append({**base, 'choices': [{'index': 0, 'finish_reason': None,
                                            'delta': {'reasoning_content': msg['reasoning_content']}}]})
    if msg.get('content'):
        chunks.append({**base, 'choices': [{'index': 0, 'finish_reason': None,
                                            'delta': {'content': msg['content']}}]})
    for i, tc in enumerate(msg.get('tool_calls') or []):
        chunks.append({**base, 'choices': [{'index': 0, 'finish_reason': None, 'delta': {
            'tool_calls': [{'index': i, **tc}]}}]})
    final = {**base, 'choices': [{'index': 0, 'delta': {},
                                  'finish_reason': choice.get('finish_reason') or 'stop'}]}
    if completion.get('usage'):
        final['usage'] = completion['usage']
    chunks.append(final)
    return chunks


def sse(chunks: list[dict]) -> Iterator[str]:
    for c in chunks:
        yield f'data: {json.dumps(c)}\n\n'
    yield 'data: [DONE]\n\n'


# --------------------------------------------------------------- upstream

def _upstream_chunks(body: dict, cancel: threading.Event) -> Iterator[dict]:
    """Stream from llama-server, raising Preempted the moment `cancel` fires.
    Closing the response in the finally is what frees the server's slot."""
    url = f"{get_provider_config()['llama_url'].rstrip('/')}/v1/chat/completions"
    try:
        resp = requests.post(url, json=body, stream=True, timeout=UPSTREAM_TIMEOUT)
    except requests.RequestException as e:
        raise UpstreamError(f'llama-server is unreachable: {e}') from e
    try:
        if resp.status_code != 200:
            raise UpstreamError(f'llama-server answered {resp.status_code}: {resp.text[:300]}')
        for line in resp.iter_lines(decode_unicode=True):
            if cancel.is_set():
                raise service.Preempted('cancelled for an interactive call')
            if not line or not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            try:
                yield json.loads(data)
            except ValueError:
                continue
    finally:
        resp.close()


class Generator:
    """One request through the service, with replay on preemption and the
    pause held up to a limit. `upstream` is the seam tests replace."""

    def __init__(self, on_event: EventFn, upstream=_upstream_chunks,
                 pause_wait: float = PAUSE_WAIT_SECONDS):
        self.on_event = on_event
        self.upstream = upstream
        self.pause_wait = pause_wait

    def once(self, body: dict) -> dict:
        alias = get_model()
        lane = service.lane_for(alias)
        upstream_body = prepare(body, alias)
        cancels = 0
        paused_since = None
        while True:
            started = time.monotonic()
            try:
                with service.slot(lane=lane, label=LABEL, priority=service.Priority.BACKGROUND,
                                  preemptible=cancels < MAX_CANCELS) as cancel:
                    admitted = time.monotonic()
                    completion = assemble(self.upstream(upstream_body, cancel))
                self.on_event('proxy', {
                    'action': 'call', 'waited': round(admitted - started, 2),
                    'ran': round(time.monotonic() - admitted, 2),
                    'cancels': cancels, 'usage': completion.get('usage'),
                    'finish': completion['choices'][0].get('finish_reason'),
                    **cache_stats(completion),
                })
                completion.pop('timings', None)
                return completion
            except service.Preempted:
                cancels += 1
                self.on_event('proxy', {'action': 'preempted', 'cancels': cancels})
            except service.InferencePaused:
                if paused_since is None:
                    paused_since = time.monotonic()
                    self.on_event('proxy', {'action': 'paused'})
                if time.monotonic() - paused_since >= self.pause_wait:
                    self.on_event('proxy', {'action': 'paused_out'})
                    raise
                time.sleep(min(PAUSE_POLL_SECONDS, self.pause_wait / 10))

    def complete(self, body: dict) -> dict:
        """`once`, then repair a tool call stranded in the reasoning."""
        completion = self.once(body)
        for attempt in range(POKE_LIMIT + 1):
            verdict = toolfix.check(body, completion)
            if verdict.action == 'ok':
                return completion
            if verdict.action == 'promote':
                self.on_event('proxy', {'action': 'promoted', 'reason': verdict.reason,
                                        'tools': [c['name'] for c in verdict.calls]})
                return verdict.response
            if attempt == POKE_LIMIT:
                self.on_event('proxy', {'action': 'stuck', 'reason': verdict.reason})
                return completion
            self.on_event('proxy', {'action': 'poked', 'reason': verdict.reason,
                                    'attempt': attempt + 1})
            completion = self.once({**body, 'messages': toolfix.poke_messages(body, verdict)})
        return completion


# ------------------------------------------------------------- the server

def make_app(gen: Generator, lookups=None) -> Flask:
    """The agent's endpoint. `lookups` (backend/codeagent/lookups.py) adds the
    `/lunaschal/tools` routes its pi extension calls; without it they list
    nothing, which the extension reads as "no tools to register"."""
    app = Flask('codeagent-proxy')

    @app.get('/lunaschal/tools')
    def lookup_tools():
        return jsonify({'tools': lookups.offered() if lookups else []})

    @app.post('/lunaschal/tools/<name>')
    def lookup_call(name):
        if lookups is None or name not in lookups.names:
            return jsonify({'text': f'No lookup tool named {name!r}.', 'ok': False}), 404
        args = request.get_json(silent=True) or {}
        results: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: results.put(lookups.call(name, args)), daemon=True,
                         name='codeagent-lookup').start()

        def body():
            # A web run takes minutes, and Node's fetch gives up on a response
            # that sends nothing for five. Leading whitespace is valid JSON, so
            # the keepalive needs no framing the extension has to strip.
            while True:
                try:
                    _status, result = results.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    yield ' '
                    continue
                yield json.dumps(result)
                return

        return Response(body(), mimetype='application/json',
                        headers={'Cache-Control': 'no-cache'})

    @app.get('/v1/models')
    def models():
        return jsonify({'object': 'list', 'data': [
            {'id': MODEL_NAME, 'object': 'model', 'owned_by': 'lunaschal'}]})

    @app.post('/v1/chat/completions')
    def completions():
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get('messages'), list):
            return jsonify({'error': {'message': 'messages is required'}}), 400
        if not body.get('stream'):
            try:
                return jsonify(gen.complete(body))
            except service.InferencePaused:
                return jsonify({'error': {'message': service.PAUSED_MESSAGE}}), 503
            except UpstreamError as e:
                return jsonify({'error': {'message': str(e)}}), 502

        results: queue.Queue = queue.Queue()

        def work():
            try:
                results.put(('ok', gen.complete(body)))
            except service.InferencePaused:
                results.put(('error', service.PAUSED_MESSAGE))
            except Exception as e:  # reported to the agent as a stream error
                logger.exception('code agent proxy call failed')
                results.put(('error', str(e)))

        threading.Thread(target=work, daemon=True, name='codeagent-proxy-call').start()

        def stream():
            while True:
                try:
                    kind, value = results.get(timeout=KEEPALIVE_SECONDS)
                except queue.Empty:
                    # An SSE comment: ignored by every client, but it keeps an
                    # idle-read timeout from firing during a long generation.
                    yield ': keepalive\n\n'
                    continue
                if kind == 'ok':
                    yield from sse(to_chunks(value))
                else:
                    yield f"data: {json.dumps({'error': {'message': value}})}\n\n"
                return

        return Response(stream(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache'})

    return app


class ProxyServer:
    """One task's proxy, serving on its socket from a daemon thread."""

    def __init__(self, socket_path: Path, on_event: EventFn, upstream=_upstream_chunks,
                 lookups=None):
        from werkzeug.serving import make_server
        self.socket_path = Path(socket_path)
        self.lookups = lookups
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.app = make_app(Generator(on_event, upstream), lookups)
        self._server = make_server(f'unix://{self.socket_path}', 0, self.app, threaded=True)
        # The container runs as this same uid; nobody else needs it.
        os.chmod(self.socket_path, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True,
                                        name=f'codeagent-proxy-{self.socket_path.parent.parent.name}')

    def start(self) -> 'ProxyServer':
        self._thread.start()
        return self

    def stop(self) -> None:
        if self.lookups is not None:
            self.lookups.stopped.set()  # a web run in flight stops at its next step
        self._server.shutdown()
        self._server.server_close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
