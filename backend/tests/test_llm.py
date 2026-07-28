"""Tests for the provider-agnostic JSON-mode parse helper in backend.ai.llm.

Reasoning models (and models that ignore JSON mode) can return empty content or
wrap the object in a ```json fence; a bare json.loads then blows up with an
opaque "Expecting value" error. `_parse_json_response` normalizes those cases.
"""
import pytest

from backend.ai import llm
from backend.ai.llm import _parse_json_response, EmptyCompletion, chat_json, _native_body


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


# --- native /api/chat body construction ---

def test_native_body_json_grammar_when_not_thinking():
    body = _native_body([], model='m', reasoning_effort='none', num_ctx=8192,
                        num_predict=2048, json_format=True)
    assert body['think'] is False
    assert body['format'] == 'json'          # grammar constraint applied
    assert body['options'] == {'num_ctx': 8192, 'num_predict': 2048}


def test_native_body_drops_grammar_and_sets_think_when_reasoning():
    body = _native_body([], model='m', reasoning_effort='low', num_ctx=16384,
                        num_predict=4096, json_format=True)
    # Grammar dropped (collides with thinking); think carries the level.
    assert 'format' not in body
    assert body['think'] == 'low'


def test_native_body_omits_options_when_unset():
    body = _native_body([], model='m', reasoning_effort='none', num_ctx=None,
                        num_predict=None)
    assert 'options' not in body


def _stub_native(monkeypatch, captured):
    """Capture the kwargs chat_* pass into the native transport."""
    def fake_native_chat(messages, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return '{"ok": true}'
    monkeypatch.setattr(llm, 'get_provider_config', lambda: {'ollama_model': 'm'})
    monkeypatch.setattr(llm, '_native_chat', fake_native_chat)


def test_chat_json_forwards_reasoning_and_num_ctx(monkeypatch):
    captured = {}
    _stub_native(monkeypatch, captured)
    chat_json('hi', reasoning_effort='high', num_ctx=16384)
    assert captured['reasoning_effort'] == 'high'
    assert captured['num_ctx'] == 16384
    assert captured['json_format'] is True


def test_chat_json_coerces_invalid_reasoning_effort_to_none(monkeypatch):
    captured = {}
    _stub_native(monkeypatch, captured)
    chat_json('hi', reasoning_effort='turbo')
    assert captured['reasoning_effort'] == 'none'


def test_default_generation_opts_reads_settings(client):
    from backend.ai.llm import LLM_MAX_TOKENS, LLM_NUM_CTX, default_generation_opts
    from backend.db import connection

    # No settings row -> hard defaults.
    assert default_generation_opts() == {
        'reasoning_effort': 'none',
        'num_ctx': LLM_NUM_CTX,
        'num_predict': LLM_MAX_TOKENS,
    }

    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute(
        'UPDATE settings SET llm_reasoning_effort=?, llm_max_tokens=?, llm_num_ctx=? WHERE id=1',
        ('medium', 2048, 12288),
    )
    db.commit()
    assert default_generation_opts() == {
        'reasoning_effort': 'medium', 'num_ctx': 12288, 'num_predict': 2048,
    }


def test_default_generation_opts_coerces_invalid_effort(client):
    from backend.ai.llm import default_generation_opts
    from backend.db import connection

    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute("UPDATE settings SET llm_reasoning_effort='bogus' WHERE id=1")
    db.commit()
    assert default_generation_opts()['reasoning_effort'] == 'none'


def test_chat_text_applies_default_generation_opts(client, monkeypatch):
    captured = {}
    _stub_native(monkeypatch, captured)
    from backend.db import connection
    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)'
    )
    db.execute(
        'UPDATE settings SET llm_reasoning_effort=?, llm_max_tokens=?, llm_num_ctx=? WHERE id=1',
        ('low', 1234, 9000),
    )
    db.commit()

    llm.chat_text('hello')
    assert captured['reasoning_effort'] == 'low'
    assert captured['num_predict'] == 1234
    assert captured['num_ctx'] == 9000
