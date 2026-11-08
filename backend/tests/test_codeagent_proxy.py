"""The coding agent's model proxy, against a fake upstream and the real service."""
import http.client
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from backend.ai import service
from backend.codeagent import proxy, toolfix
from backend.db.connection import get_db

TOOLS = [{'type': 'function', 'function': {
    'name': 'bash', 'parameters': {'type': 'object', 'properties': {
        'command': {'type': 'string'}}, 'required': ['command']}}}]
BODY = {'model': 'local', 'messages': [{'role': 'user', 'content': 'hi'}], 'tools': TOOLS}


def chunks(content='', reasoning='', tool=None, finish='stop'):
    out = [{'id': 'c1', 'created': 1, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]}]
    if reasoning:
        out.append({'choices': [{'index': 0, 'delta': {'reasoning_content': reasoning[:5]}}]})
        out.append({'choices': [{'index': 0, 'delta': {'reasoning_content': reasoning[5:]}}]})
    if content:
        out.append({'choices': [{'index': 0, 'delta': {'content': content}}]})
    if tool:
        out.append({'choices': [{'index': 0, 'delta': {'tool_calls': [
            {'index': 0, 'id': 't1', 'function': {'name': tool, 'arguments': '{"comm'}}]}}]})
        out.append({'choices': [{'index': 0, 'delta': {'tool_calls': [
            {'index': 0, 'function': {'arguments': 'and": "ls"}'}}]}}]})
    out.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 3}})
    return out


class Upstream:
    """Scripted llama-server. Each script entry is a chunk list, or 'block' to
    generate until cancelled — which is how a preemption looks from here."""

    def __init__(self, *script):
        self.script = list(script)
        self.bodies = []

    def __call__(self, body, cancel):
        self.bodies.append(body)
        step = self.script.pop(0)
        if step == 'block':
            while not cancel.wait(0.01):
                pass
            raise service.Preempted('cancelled')
        yield from step


@pytest.fixture(autouse=True)
def clean_service():
    service.reset()
    yield
    service.reset()


@pytest.fixture
def events():
    return []


def gen_for(upstream, events, **kw):
    return proxy.Generator(lambda kind, p: events.append(p), upstream, **kw)


def test_assemble_and_to_chunks_round_trip():
    done = proxy.assemble(chunks(content='hello', reasoning='thinking hard', tool='bash'))
    msg = done['choices'][0]['message']
    assert msg['content'] == 'hello' and msg['reasoning_content'] == 'thinking hard'
    assert msg['tool_calls'][0]['function'] == {'name': 'bash', 'arguments': '{"command": "ls"}'}
    assert done['usage']['completion_tokens'] == 3
    again = proxy.assemble(proxy.to_chunks(done))
    assert again['choices'] == done['choices']


def test_prepare_rewrites_the_model_and_floors_max_tokens():
    out = proxy.prepare({**BODY, 'max_tokens': 8192, 'stream': False}, 'qwen36')
    assert out['model'] == 'qwen36' and out['stream'] is True
    assert out['max_tokens'] == proxy.MIN_MAX_TOKENS
    assert out['stream_options'] == {'include_usage': True}
    assert BODY['model'] == 'local'  # not mutated


def test_a_plain_call_is_logged_with_its_timings(events):
    gen = gen_for(Upstream(chunks(content='ok')), events)
    done = gen.complete(BODY)
    assert done['choices'][0]['message']['content'] == 'ok'
    [ev] = events
    assert ev['action'] == 'call' and ev['cancels'] == 0 and 'waited' in ev and 'ran' in ev


def test_a_preempted_call_is_replayed_unchanged(events):
    up = Upstream('block', chunks(content='second try'))
    gen = gen_for(up, events)
    result = {}
    t = threading.Thread(target=lambda: result.update(done=gen.once(BODY)))
    t.start()
    deadline = time.time() + 2
    while not up.bodies and time.time() < deadline:
        time.sleep(0.01)
    # A chat message arrives: it takes the lane, the agent's call is cancelled.
    with service.slot(lane=service.GPU, label='chat', priority=service.Priority.INTERACTIVE):
        time.sleep(0.05)
    t.join(3)
    assert result['done']['choices'][0]['message']['content'] == 'second try'
    assert [e['action'] for e in events] == ['preempted', 'call']
    assert events[-1]['cancels'] == 1
    assert up.bodies[0] == up.bodies[1]


def test_a_long_pause_ends_in_inference_paused(events):
    get_db().execute('UPDATE settings SET inference_paused=1')
    get_db().commit()
    service.invalidate_pause_cache()
    gen = gen_for(Upstream(), events, pause_wait=0.05)
    with pytest.raises(service.InferencePaused):
        gen.once(BODY)
    assert [e['action'] for e in events] == ['paused', 'paused_out']


CALL = '<tool_call>\n<function=bash>\n<parameter=command>\nls\n</parameter>\n</function>\n</tool_call>'


def test_a_call_in_the_reasoning_is_promoted(events):
    gen = gen_for(Upstream(chunks(reasoning=f'Let me list.\n{CALL}')), events)
    done = gen.complete(BODY)
    assert done['choices'][0]['message']['tool_calls'][0]['function']['name'] == 'bash'
    assert events[-1]['action'] == 'promoted'


def test_a_broken_call_is_poked_then_succeeds(events):
    broken = CALL.replace('</tool_call>', '')
    up = Upstream(chunks(reasoning=f'Let me list.\n{broken}'), chunks(tool='bash', finish='tool_calls'))
    done = gen_for(up, events).complete(BODY)
    assert done['choices'][0]['message']['tool_calls']
    assert [e['action'] for e in events] == ['call', 'poked', 'call']
    assert up.bodies[1]['messages'][-1]['content'] == toolfix.POKE_TEXT


def test_gives_up_after_the_poke_limit(events):
    broken = chunks(reasoning='x' + CALL.replace('</tool_call>', ''))
    up = Upstream(*[broken] * (proxy.POKE_LIMIT + 1))
    gen_for(up, events).complete(BODY)
    actions = [e['action'] for e in events if e['action'] != 'call']
    assert actions == ['poked'] * proxy.POKE_LIMIT + ['stuck']


def test_http_non_streaming(events):
    app = proxy.make_app(gen_for(Upstream(chunks(content='hi there')), events))
    resp = app.test_client().post('/v1/chat/completions', json={**BODY, 'stream': False})
    assert resp.status_code == 200
    assert resp.get_json()['choices'][0]['message']['content'] == 'hi there'


def test_http_streaming_sends_keepalives_then_the_answer(events, monkeypatch):
    monkeypatch.setattr(proxy, 'KEEPALIVE_SECONDS', 0.02)

    def slow(body, cancel):
        time.sleep(0.1)
        yield from chunks(content='late')
    app = proxy.make_app(gen_for(slow, events))
    resp = app.test_client().post('/v1/chat/completions', json={**BODY, 'stream': True})
    text = resp.get_data(as_text=True)
    assert text.startswith(': keepalive')
    frames = [json.loads(line[6:]) for line in text.splitlines()
              if line.startswith('data: {')]
    assert proxy.assemble(frames)['choices'][0]['message']['content'] == 'late'
    assert text.rstrip().endswith('data: [DONE]')


def test_http_rejects_a_body_without_messages(events):
    app = proxy.make_app(gen_for(Upstream(), events))
    assert app.test_client().post('/v1/chat/completions', json={}).status_code == 400


def test_models_lists_the_local_name(events):
    app = proxy.make_app(gen_for(Upstream(), events))
    assert app.test_client().get('/v1/models').get_json()['data'][0]['id'] == 'local'


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost')
        self.path_ = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path_)


def test_serves_over_a_unix_socket(events):
    # Short path: AF_UNIX paths are limited to ~104 bytes on macOS.
    path = Path(tempfile.mkdtemp(dir='/tmp')) / 'model.sock'
    server = proxy.ProxyServer(path, lambda k, p: events.append(p),
                               Upstream(chunks(content='over the socket'))).start()
    try:
        assert oct(path.stat().st_mode & 0o777) == '0o600'
        conn = UnixHTTPConnection(str(path))
        conn.request('POST', '/v1/chat/completions', body=json.dumps(BODY),
                     headers={'Content-Type': 'application/json'})
        body = json.loads(conn.getresponse().read())
        assert body['choices'][0]['message']['content'] == 'over the socket'
    finally:
        server.stop()
    assert not path.exists()


def test_prepare_fills_the_coding_sampler_without_overriding_the_client():
    out = proxy.prepare(BODY, 'qwen36')
    assert out['temperature'] == 0.6 and out['top_p'] == 0.95 and out['top_k'] == 20
    assert out['min_p'] == 0.0 and out['presence_penalty'] == 0.0
    own = proxy.prepare({**BODY, 'temperature': 0.2, 'chat_template_kwargs': {'preserve_thinking': True}},
                        'qwen36')
    assert own['temperature'] == 0.2
    assert own['chat_template_kwargs'] == {'preserve_thinking': True}


def test_a_call_logs_what_was_prefilled_and_what_came_from_the_cache(events):
    script = chunks(content='ok')
    script[-1]['timings'] = {'prompt_n': 1200, 'cache_n': 148000, 'predicted_per_second': 33.9}
    done = gen_for(Upstream(script), events).complete(BODY)
    [ev] = events
    assert ev['prefilled'] == 1200 and ev['cached'] == 148000
    assert 'timings' not in done  # ours, never the agent's
