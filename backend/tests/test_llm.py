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
    # No ceiling to add the budget to, so none is invented.
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


def test_request_kwargs_sends_a_thinking_budget_and_adds_it_to_the_ceiling():
    """llama-server counts thinking against max_tokens, so the budget is added
    on top: max_tokens keeps meaning room for the answer, and a long think can
    no longer spend it."""
    kwargs = _request_kwargs(thinking=True, max_tokens=2048, thinking_budget=4096)
    assert kwargs['extra_body']['reasoning_budget_tokens'] == 4096
    assert kwargs['extra_body']['reasoning_budget_message'] == llm.THINKING_BUDGET_MESSAGE
    assert kwargs['max_tokens'] == 2048 + 4096 + llm.THINKING_BUDGET_OVERHEAD


def test_request_kwargs_ignores_a_budget_while_thinking_is_off():
    kwargs = _request_kwargs(thinking=False, max_tokens=2048, thinking_budget=4096)
    assert 'reasoning_budget_tokens' not in kwargs['extra_body']
    assert 'reasoning_budget_message' not in kwargs['extra_body']
    assert kwargs['max_tokens'] == 2048


def test_explicit_thinking_without_a_budget_is_capped_for_a_waiting_caller():
    """Every think is capped, so the answer's room can be kept on top of it.
    Unbudgeted, Qwen3.6 thought for 34 s about a trivial question."""
    kwargs = _request_kwargs(thinking=True, max_tokens=2048)
    assert kwargs['extra_body']['reasoning_budget_tokens'] == llm.INTERACTIVE_THINKING_BUDGET
    assert kwargs['max_tokens'] == (
        2048 + llm.INTERACTIVE_THINKING_BUDGET + llm.THINKING_BUDGET_OVERHEAD
    )


def test_explicit_thinking_without_a_budget_is_capped_in_the_background():
    from backend.ai import service
    with service.background():
        kwargs = _request_kwargs(thinking=True, max_tokens=2048)
    assert kwargs['extra_body']['reasoning_budget_tokens'] == llm.BACKGROUND_THINKING_BUDGET


def test_chat_json_passes_the_thinking_budget_through(monkeypatch):
    captured = _stub_client(monkeypatch, {})
    llm.chat_json('hi', thinking=True, thinking_budget=1024, max_tokens=512)
    assert captured['extra_body']['reasoning_budget_tokens'] == 1024
    assert captured['max_tokens'] == 1536 + llm.THINKING_BUDGET_OVERHEAD


# --- Thinking decided by the thread ---
#
# Calls that don't choose thinking for themselves are decided by where they
# run: off by default (somebody may be waiting), long inside an overnight pass's
# scope (nobody is).

def _lanes(monkeypatch):
    """qwen36 is the GPU chat model; anything else is the CPU lane."""
    from backend.ai import service
    monkeypatch.setattr(service, 'lane_for',
                        lambda alias: service.GPU if alias == 'qwen36' else service.CPU)


def test_a_thinking_scope_turns_thinking_on_for_undecided_calls(monkeypatch):
    captured = _stub_client(monkeypatch, {})
    _lanes(monkeypatch)
    with llm.thinking_scope(8192):
        llm.chat_json('hi', max_tokens=1000)
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is True
    assert captured['extra_body']['reasoning_budget_tokens'] == 8192
    # The answer keeps its whole 1000 tokens on top of the think.
    assert captured['max_tokens'] == 1000 + 8192 + llm.THINKING_BUDGET_OVERHEAD


def test_a_scope_without_a_budget_means_no_thinking(monkeypatch):
    captured = _stub_client(monkeypatch, {})
    _lanes(monkeypatch)
    with llm.thinking_scope(None):
        llm.chat_json('hi', max_tokens=1000)
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is False
    assert captured['max_tokens'] == 1000


def test_an_explicit_choice_beats_the_scope(monkeypatch):
    captured = _stub_client(monkeypatch, {})
    _lanes(monkeypatch)
    with llm.thinking_scope(8192):
        llm.chat_json('hi', thinking=False)
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is False


def test_the_scope_skips_the_cpu_lane(monkeypatch):
    """The CPU model has a 16k context; a long think beside an image would not
    fit in it."""
    captured = _stub_client(monkeypatch, {})
    _lanes(monkeypatch)
    with llm.thinking_scope(8192):
        llm.chat_json('hi', model='gemma4-12b-omni')
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is False
    assert 'reasoning_budget_tokens' not in captured['extra_body']


def test_scopes_nest_and_restore():
    with llm.thinking_scope(8192):
        with llm.thinking_scope(None):
            assert _request_kwargs(thinking=None, max_tokens=10)['max_tokens'] == 10
        assert llm._scope.budget == 8192
    assert not hasattr(llm._scope, 'budget')


def test_a_scope_belongs_to_its_thread(monkeypatch):
    import threading
    _lanes(monkeypatch)
    monkeypatch.setattr(llm, 'get_provider_config', lambda: {})
    monkeypatch.setattr(llm, 'get_model', lambda *a, **k: 'qwen36')
    seen = {}
    with llm.thinking_scope(8192):
        t = threading.Thread(target=lambda: seen.update(
            _request_kwargs(thinking=None, max_tokens=10)['extra_body']))
        t.start()
        t.join()
    assert seen['chat_template_kwargs']['enable_thinking'] is False


def test_overnight_thinking_follows_the_settings(client, monkeypatch):
    from backend.db import connection
    _lanes(monkeypatch)
    monkeypatch.setattr(llm, 'get_provider_config', lambda: {})
    monkeypatch.setattr(llm, 'get_model', lambda *a, **k: 'qwen36')

    def think():
        return _request_kwargs(thinking=None, max_tokens=10)['extra_body']

    # Off by default: the scope opens with no budget.
    with llm.overnight_thinking():
        assert think()['chat_template_kwargs']['enable_thinking'] is False

    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute('UPDATE settings SET briefing_thinking=1 WHERE id=1')
    db.commit()
    with llm.overnight_thinking():
        assert think()['reasoning_budget_tokens'] == llm.OVERNIGHT_THINKING_BUDGET == 49152

    db.execute('UPDATE settings SET briefing_thinking_budget=2048 WHERE id=1')
    db.commit()
    with llm.overnight_thinking():
        assert think()['reasoning_budget_tokens'] == 2048


def test_an_overnight_call_comes_to_about_sixty_thousand_tokens():
    """48k of thinking on top of a typical answer ceiling: the cap stops a
    runaway rather than rationing reasoning, and the answer keeps its room."""
    kwargs = _request_kwargs(thinking=True, max_tokens=12_000,
                             thinking_budget=llm.OVERNIGHT_THINKING_BUDGET)
    assert kwargs['max_tokens'] == 12_000 + 49152 + llm.THINKING_BUDGET_OVERHEAD


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
    # The setting's ceiling stays room for the answer; the short interactive
    # think is added on top.
    assert captured['max_tokens'] == (
        1234 + llm.INTERACTIVE_THINKING_BUDGET + llm.THINKING_BUDGET_OVERHEAD
    )
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is True
    assert captured['extra_body']['reasoning_budget_tokens'] == llm.INTERACTIVE_THINKING_BUDGET


def test_the_chat_thinking_setting_does_not_reach_background_calls(client, monkeypatch):
    """`llm_thinking` is a preference about waiting. A P2 call through the same
    helper is nobody's wait, and stays off outside an overnight scope."""
    from backend.ai import service
    from backend.db import connection
    captured = _stub_client(monkeypatch, {}, content='hello there')
    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute('UPDATE settings SET llm_thinking=1, llm_max_tokens=1234 WHERE id=1')
    db.commit()

    with service.background():
        assert llm.default_generation_opts()['thinking'] is None
        llm.chat_text('hello')
    assert captured['extra_body']['chat_template_kwargs']['enable_thinking'] is False
    assert captured['max_tokens'] == 1234


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
