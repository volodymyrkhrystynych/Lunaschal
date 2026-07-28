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


def test_polish_sends_the_system_prompt_and_keeps_paragraphs(monkeypatch):
    # The whole point of the polish pass is turning a dictated wall of text into
    # paragraphs, and the journal view renders with whitespace-pre-wrap — so the
    # blank lines have to survive the output cleaning untouched.
    polished = 'So today was rough. I barely slept.\n\nAnyway, the parser works.'
    captured = _fake_openai(monkeypatch, polished)
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config())

    result = journal.polish_journal_entry('so today was rough i barely slept')

    assert result == polished
    system, user = captured['kwargs']['messages']
    assert system == {'role': 'system', 'content': journal._SYSTEM}
    assert user['content'] == 'so today was rough i barely slept'


def test_metadata_uses_configured_model_no_cpu_options(monkeypatch):
    captured = _fake_openai(monkeypatch, '{"title": "A title", "tags": ["work"]}')
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config())

    result = journal.generate_journal_metadata('some content')

    assert result == {'title': 'A title', 'tags': ['work']}
    assert captured['kwargs']['model'] == 'llama3.2'
    assert 'extra_body' not in captured['kwargs']
    assert captured['kwargs']['response_format'] == {'type': 'json_object'}


class TestMetadataTagNormalization:
    """Tags reach the frontend as React keys, so duplicates are a render bug.
    Normalization is the backend's job (see src/lib/tags.ts)."""

    def _meta(self, monkeypatch, payload):
        _fake_openai(monkeypatch, payload)
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'get_provider_config', lambda: _ollama_config())
        return journal.generate_journal_metadata('some content')

    def test_dedupes_repeated_tags(self, monkeypatch):
        result = self._meta(monkeypatch, '{"title": "T", "tags": ["reading", "reading", "mood"]}')
        assert result['tags'] == ['reading', 'mood']

    def test_dedupes_case_variants(self, monkeypatch):
        result = self._meta(monkeypatch, '{"title": "T", "tags": ["Reading", "reading"]}')
        assert result['tags'] == ['reading']

    def test_caps_at_three_after_deduping(self, monkeypatch):
        # Dedupe first, so repeats don't burn slots real tags could have used.
        result = self._meta(
            monkeypatch, '{"title": "T", "tags": ["a", "a", "b", "c", "d"]}')
        assert result['tags'] == ['a', 'b', 'c']

    def test_bare_string_is_not_iterated_by_character(self, monkeypatch):
        result = self._meta(monkeypatch, '{"title": "T", "tags": "reading"}')
        assert result['tags'] is None


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


class TestCleanPolishOutput:
    """Unit tests for the preamble-stripping / quote-unwrapping heuristics
    applied to raw LLM output before it's saved as a journal entry."""

    def test_passes_through_clean_text_unchanged(self):
        text = 'First paragraph.\n\nSecond paragraph.'
        assert journal._clean_polish_output(text) == text

    def test_strips_leading_preamble_line(self):
        text = "Here is the corrected text:\n\nActual entry content."
        assert journal._clean_polish_output(text) == 'Actual entry content.'

    def test_strips_preamble_with_lead_in_phrase(self):
        text = "Sure, here's the corrected version:\nActual entry content."
        assert journal._clean_polish_output(text) == 'Actual entry content.'

    def test_unwraps_single_paragraph_wrapped_in_quotes(self):
        text = '"Actual entry content."'
        assert journal._clean_polish_output(text) == 'Actual entry content.'

    def test_unwraps_quotes_around_entire_multi_paragraph_output(self):
        text = '"First paragraph.\n\nSecond paragraph."'
        assert journal._clean_polish_output(text) == 'First paragraph.\n\nSecond paragraph.'

    def test_unwraps_curly_quotes_around_entire_multi_paragraph_output(self):
        text = '“First paragraph.\n\nSecond paragraph.”'
        assert journal._clean_polish_output(text) == 'First paragraph.\n\nSecond paragraph.'

    def test_unwraps_quotes_per_paragraph(self):
        text = '"First paragraph."\n\n"Second paragraph."'
        assert journal._clean_polish_output(text) == 'First paragraph.\n\nSecond paragraph.'

    def test_preserves_legitimate_quote_that_does_not_wrap_whole_paragraph(self):
        text = 'She said "hello" to me.'
        assert journal._clean_polish_output(text) == text

    def test_strips_preamble_and_wrapping_quotes_together(self):
        text = 'Here is the corrected text:\n"First paragraph.\n\nSecond paragraph."'
        assert journal._clean_polish_output(text) == 'First paragraph.\n\nSecond paragraph.'
