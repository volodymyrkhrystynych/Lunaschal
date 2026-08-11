"""Tests for backend.ai.llm: JSON parsing and llama-server request construction.

`_parse_json_response` is now a fallback rather than the main path — with a schema
attached llama-server guarantees well-formed JSON — but it still covers the
schema-less calls and thinking models that leak a <think> block or a ```json
fence, either of which makes a bare json.loads blow up with an opaque
"Expecting value" error.
"""
import pytest

from backend.ai import llm
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


class _FakeCompletions:
    """Minimal stand-in for client.chat.completions, capturing the call."""

    def __init__(self, captured, content='{"ok": true}'):
        self.captured = captured
        self.content = content

    def create(self, **kwargs):
        self.captured.clear()
        self.captured.update(kwargs)
        message = type('M', (), {'content': self.content})()
        choice = type('C', (), {'message': message})()
        return type('R', (), {'choices': [choice]})()


def _stub_client(monkeypatch, captured, content='{"ok": true}'):
    completions = _FakeCompletions(captured, content)
    client = type('Client', (), {'chat': type('Chat', (), {'completions': completions})()})()
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
