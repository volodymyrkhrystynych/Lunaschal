"""Unit tests for `backend.ai.writing.summarize_discussion` with a faked
OpenAI-compatible client (Ollama provider config) — no real LLM calls."""
from types import SimpleNamespace

import pytest

from backend.ai import writing


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


def _configure_ollama(monkeypatch):
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(writing, 'get_provider_config', lambda: {
        'ollama_url': 'http://localhost:11434',
        'ollama_model': 'llama3.2',
    })


def test_summarize_discussion_parses_result(monkeypatch):
    captured = _fake_openai(
        monkeypatch, '{"title": "Villain twist", "content": "- Brother is the villain"}'
    )
    _configure_ollama(monkeypatch)

    result = writing.summarize_discussion(
        'Author: what if...\n\nAssistant: yes', 'My Story', 'A tale'
    )

    assert result == {'title': 'Villain twist', 'content': '- Brother is the villain'}
    kwargs = captured['kwargs']
    assert kwargs['model'] == 'llama3.2'
    assert kwargs['response_format'] == {'type': 'json_object'}
    system = kwargs['messages'][0]
    assert system['role'] == 'system'
    assert 'My Story' in system['content']
    assert 'A tale' in system['content']
    assert kwargs['messages'][1]['content'] == 'Author: what if...\n\nAssistant: yes'


def test_summarize_discussion_truncates_keeping_tail(monkeypatch):
    captured = _fake_openai(monkeypatch, '{"title": "T", "content": "C"}')
    _configure_ollama(monkeypatch)

    transcript = 'OLD ' * 10000 + 'RECENT DECISION'
    writing.summarize_discussion(transcript, 'My Story')

    sent = captured['kwargs']['messages'][1]['content']
    assert len(sent) == writing._MAX_INPUT_CHARS
    assert sent.endswith('RECENT DECISION')


def test_summarize_discussion_malformed_json(monkeypatch):
    _fake_openai(monkeypatch, 'not json at all')
    _configure_ollama(monkeypatch)

    assert writing.summarize_discussion('Author: hi', 'My Story') is None


def test_summarize_discussion_missing_fields(monkeypatch):
    _fake_openai(monkeypatch, '{"title": "Only a title"}')
    _configure_ollama(monkeypatch)

    assert writing.summarize_discussion('Author: hi', 'My Story') is None


def test_summarize_discussion_empty_transcript(monkeypatch):
    _configure_ollama(monkeypatch)
    assert writing.summarize_discussion('   ', 'My Story') is None


def test_summarize_discussion_unconfigured(monkeypatch):
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: False)
    assert writing.summarize_discussion('Author: hi', 'My Story') is None
