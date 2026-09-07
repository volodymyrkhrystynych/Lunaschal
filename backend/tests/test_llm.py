"""Tests for backend.ai.llm: JSON parsing and llama-server request construction.

`_parse_json_response` is now a fallback rather than the main path — with a schema
attached llama-server guarantees well-formed JSON — but it still covers the
schema-less calls and thinking models that leak a <think> block or a ```json
fence, either of which makes a bare json.loads blow up with an opaque
"Expecting value" error.
"""
import json

import pytest

from backend.ai import llm
from backend.tests.streamfakes import (
    FakeClient, chunk, text_stream, tool_call_delta,
)
from backend.ai.llm import (
    _parse_json_response, _request_kwargs, EmptyCompletion,
)


def test_parses_plain_json():
    assert _parse_json_response('{"a": 1}') == {'a': 1}


def test_strips_json_fence():
    assert _parse_json_response('```json\n{"a": 1}\n```') == {'a': 1}


def test_strips_bare_fence():
    assert _parse_json_response('```\n{"a": 1}\n```') == {'a': 1}


def test_extracts_object_from_surrounding_prose():
    content = 'Sure! Here is the result:\n{"a": 1}\nHope that helps.'
    assert _parse_json_response(content) == {'a': 1}


def test_strips_think_block_then_parses_fence():
    # phi4-mini-reasoning style: an (emptied) <think> block before the JSON.
    content = '<think>\n\n</think>\n\n```json\n{"a": 1}\n```'
    assert _parse_json_response(content) == {'a': 1}


def test_think_block_only_raises_empty_completion():
    with pytest.raises(EmptyCompletion):
        _parse_json_response('<think>pondering forever</think>')


@pytest.mark.parametrize('content', [None, '', '   ', '\n\n'])
def test_empty_content_raises_empty_completion(content):
    with pytest.raises(EmptyCompletion):
        _parse_json_response(content)


def test_non_json_content_raises_empty_completion():
    with pytest.raises(EmptyCompletion):
        _parse_json_response('I cannot help with that.')



# --- OpenAI-style request construction ---
#
# These replace the old Ollama `_native_body` tests. Two things they pin down that
# the migration could plausibly get wrong: thinking must be sent *explicitly*
# (Gemma 4's chat template defaults it on, so omitting the kwarg silently enables
# reasoning on every call), and no request may carry a context-window field —
# llama-server fixes the window at load time and a stray `num_ctx` would be a
# leftover from the Ollama design.

def test_request_kwargs_disables_thinking_explicitly():
    kwargs = _request_kwargs(thinking=False, max_tokens=2048)
    assert kwargs['extra_body']['chat_template_kwargs']['enable_thinking'] is False
    assert kwargs['max_tokens'] == 2048
    assert 'response_format' not in kwargs


def test_request_kwargs_enables_thinking():
    kwargs = _request_kwargs(thinking=True, max_tokens=None)
    assert kwargs['extra_body']['chat_template_kwargs']['enable_thinking'] is True
    assert 'max_tokens' not in kwargs


def test_request_kwargs_attaches_json_schema():
    schema = {'type': 'object', 'properties': {'a': {'type': 'string'}}}
    kwargs = _request_kwargs(thinking=False, max_tokens=64, schema=schema)
    rf = kwargs['response_format']
    assert rf['type'] == 'json_schema'
    assert rf['json_schema']['schema'] is schema
    # No `strict`: it would impose OpenAI's all-properties-required subset, which
    # several call sites (recipes, food) deliberately violate with optional keys.
    assert 'strict' not in rf['json_schema']


def test_no_request_carries_a_context_window():
    for kwargs in (_request_kwargs(thinking=False, max_tokens=100),
                   _request_kwargs(thinking=True, max_tokens=None, schema={})):
        assert 'num_ctx' not in kwargs
        assert 'num_ctx' not in kwargs.get('extra_body', {})


def _stub_client(monkeypatch, captured, content='{"ok": true}'):
    """Point llm at a fake that streams `content` back as one delta.

    Every completion is issued with stream=True now — that is what lets a
    background call be abandoned mid-generation — so the double streams too.
    `captured` is filled with the kwargs of the most recent request.
    """
    client = FakeClient(text_stream(content))
    real_create = client.completions.create

    def capture(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return real_create(**kwargs)

    client.completions.create = capture
    monkeypatch.setattr(llm, 'get_llama_client', lambda *a, **k: client)
    monkeypatch.setattr(llm, 'get_provider_config', lambda: {'llama_model': 'qwen36'})
    monkeypatch.setattr(llm, 'get_model', lambda *a, **k: 'qwen36')
    return captured


def test_chat_json_sends_schema_and_thinking(monkeypatch):
    captured = _stub_client(monkeypatch, {})
    schema = {'type': 'object'}
    assert llm.chat_json('hi', thinking=True, schema=schema) == {'ok': True}
    assert captured['model'] == 'qwen36'
    assert captured['response_format']['json_schema']['schema'] is schema
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is True


def test_chat_json_defaults_thinking_off(monkeypatch):
    """Structured calls are machine-read; thinking only adds latency. And since
    Gemma 4's template defaults it on, "off" has to be sent, not omitted."""
    captured = _stub_client(monkeypatch, {})
    llm.chat_json('hi')
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is False


def test_default_generation_opts_reads_settings(client):
    from backend.ai.llm import LLM_MAX_TOKENS, default_generation_opts
    from backend.db import connection

    # No settings row -> hard defaults, thinking off.
    assert default_generation_opts() == {
        'thinking': False,
        'max_tokens': LLM_MAX_TOKENS,
    }

    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute(
        'UPDATE settings SET llm_thinking=?, llm_max_tokens=? WHERE id=1',
        (1, 2048),
    )
    db.commit()
    assert default_generation_opts() == {'thinking': True, 'max_tokens': 2048}


def test_chat_messages_applies_default_generation_opts(client, monkeypatch):
    captured = _stub_client(monkeypatch, {}, content='hello there')
    from backend.db import connection
    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute(
        'UPDATE settings SET llm_thinking=?, llm_max_tokens=? WHERE id=1', (1, 1234),
    )
    db.commit()

    assert llm.chat_text('hello') == 'hello there'
    assert captured['max_tokens'] == 1234
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is True


# --- Reassembling a tool turn from stream deltas ---
#
# The one genuinely fiddly part of issuing every request as a stream. A
# non-streamed response handed us `message.tool_calls` whole; now the name
# arrives in one chunk and the arguments as a run of JSON fragments, and the
# tool loop (backend/research/agent.py) still expects `tc.function.arguments`
# to be a complete JSON string it can json.loads.

def _tool_stub(monkeypatch, chunks):
    client = FakeClient(chunks)
    monkeypatch.setattr(llm, 'get_llama_client', lambda *a, **k: client)
    monkeypatch.setattr(llm, 'get_provider_config', lambda: {'llama_model': 'qwen36'})
    monkeypatch.setattr(llm, 'get_model', lambda *a, **k: 'qwen36')
    return client


def test_tool_call_arguments_are_reassembled_across_chunks(monkeypatch):
    _tool_stub(monkeypatch, [
        chunk(tool_calls=[tool_call_delta(0, id='call_1', name='web_search')]),
        chunk(tool_calls=[tool_call_delta(0, arguments='{"query": ')]),
        chunk(tool_calls=[tool_call_delta(0, arguments='"otters"}')]),
        chunk(finish_reason='tool_calls'),
    ])

    message, finish_reason = llm.chat_tool_turn(
        [{'role': 'user', 'content': 'hi'}], tools=[{'type': 'function'}])

    assert finish_reason == 'tool_calls'
    assert len(message.tool_calls) == 1
    call = message.tool_calls[0]
    assert call.id == 'call_1'
    assert call.function.name == 'web_search'
    assert json.loads(call.function.arguments) == {'query': 'otters'}


def test_two_tool_calls_stay_separate_and_keep_their_order(monkeypatch):
    """Keyed by the delta's index — interleaved fragments must not merge."""
    _tool_stub(monkeypatch, [
        chunk(tool_calls=[tool_call_delta(0, id='a', name='read_file')]),
        chunk(tool_calls=[tool_call_delta(1, id='b', name='list_dir')]),
        chunk(tool_calls=[tool_call_delta(0, arguments='{"path": "x"}')]),
        chunk(tool_calls=[tool_call_delta(1, arguments='{"path": "y"}')]),
    ])

    message, _ = llm.chat_tool_turn([{'role': 'user', 'content': 'hi'}],
                                    tools=[{'type': 'function'}])

    assert [c.function.name for c in message.tool_calls] == ['read_file', 'list_dir']
    assert [json.loads(c.function.arguments)['path'] for c in message.tool_calls] == ['x', 'y']


def test_a_turn_with_no_tool_calls_reports_none_not_an_empty_list(monkeypatch):
    """The tool loop tests `if not tool_calls`, and appends a plain assistant
    message when there are none — an empty list would work, but None is what a
    real response carries and what serialize_tool_calls is never handed."""
    _tool_stub(monkeypatch, [chunk('I am done.'), chunk(finish_reason='stop')])

    message, finish_reason = llm.chat_tool_turn(
        [{'role': 'user', 'content': 'hi'}], tools=[{'type': 'function'}])

    assert message.tool_calls is None
    assert message.content == 'I am done.'
    assert finish_reason == 'stop'


def test_a_truncated_tool_turn_is_distinguishable_from_a_finished_one(monkeypatch):
    """A turn cut off at the ceiling has no tool_calls either — reading that as
    'the model is finished' is how a run cut off mid-sentence reported success."""
    _tool_stub(monkeypatch, [chunk('half a th'), chunk(finish_reason='length')])

    _message, finish_reason = llm.chat_tool_turn(
        [{'role': 'user', 'content': 'hi'}], tools=[{'type': 'function'}])

    assert finish_reason == 'length'


def test_a_tool_call_fragment_with_no_name_is_dropped(monkeypatch):
    """Arguments for a call whose name never arrived cannot be dispatched, and
    a nameless entry would crash serialize_tool_calls rather than be ignored."""
    _tool_stub(monkeypatch, [
        chunk(tool_calls=[tool_call_delta(0, arguments='{"a": 1}')]),
    ])

    message, _ = llm.chat_tool_turn([{'role': 'user', 'content': 'hi'}],
                                    tools=[{'type': 'function'}])

    assert message.tool_calls is None


def test_the_blocking_helpers_still_ask_for_a_stream(monkeypatch):
    """Not cosmetic: a blocking create() cannot be abandoned from another
    thread, so preemption would be impossible without this."""
    client = _tool_stub(monkeypatch, [chunk('{"ok": true}')])
    llm.chat_json('hi')
    assert client.calls[0]['stream'] is True


def test_the_stream_is_closed_after_a_blocking_call(monkeypatch):
    """Closing is what frees the llama-server slot."""
    client = _tool_stub(monkeypatch, [chunk('{"ok": true}')])
    llm.chat_json('hi')
    assert client.completions.streams[0].closed is True
