"""Unit tests for the Ollama branches of `backend.ai.journal` — confirms the
CPU-inference path (num_gpu:0 extra_body, separate bg model) is gone and
everything just calls the single configured Ollama model normally."""
from types import SimpleNamespace

import pytest

from backend.ai import journal


def _fake_openai(monkeypatch, content: str):
    openai = pytest.importorskip('openai')
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured['kwargs'] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return captured


def _ollama_config(**overrides):
    config = {
        'provider': 'ollama',
        'ollama_url': 'http://localhost:11434',
        'ollama_model': 'llama3.2',
    }
    config.update(overrides)
    return config


def test_polish_uses_configured_model_no_cpu_options(monkeypatch):
    captured = _fake_openai(monkeypatch, 'Polished text.')
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config())

    result = journal.polish_journal_entry('raw text')

    assert result == 'Polished text.'
    assert captured['kwargs']['model'] == 'llama3.2'
    assert 'extra_body' not in captured['kwargs']


def test_metadata_uses_configured_model_no_cpu_options(monkeypatch):
    captured = _fake_openai(monkeypatch, '{"title": "A title", "tags": ["work"]}')
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config())

    result = journal.generate_journal_metadata('some content')

    assert result == {'title': 'A title', 'tags': ['work']}
    assert captured['kwargs']['model'] == 'llama3.2'
    assert 'extra_body' not in captured['kwargs']
    assert captured['kwargs']['response_format'] == {'type': 'json_object'}


def test_classify_uses_configured_model_no_cpu_options(monkeypatch):
    captured = _fake_openai(monkeypatch, 'yes')
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config())

    result = journal.classify_entry_for_tag('some content', 'work')

    assert result is True
    assert captured['kwargs']['model'] == 'llama3.2'
    assert 'extra_body' not in captured['kwargs']


def test_falls_back_to_default_model_when_unset(monkeypatch):
    captured = _fake_openai(monkeypatch, 'Polished text.')
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config(ollama_model=None))

    journal.polish_journal_entry('raw text')

    assert captured['kwargs']['model'] == journal.DEFAULT_MODELS['ollama']
