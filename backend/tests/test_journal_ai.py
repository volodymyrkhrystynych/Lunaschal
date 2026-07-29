"""Unit tests for `backend.ai.journal`'s three LLM call sites — confirms each
calls the shared chat_json/chat_text helpers (backend.ai.llm) with the right
system prompt and processes the result correctly. The native transport itself
is covered by test_llm.py; these tests fake chat_json/chat_text directly."""
from backend.ai import journal


def test_polish_passes_system_prompt_and_returns_cleaned_text(monkeypatch):
    captured = {}

    def fake_chat_text(prompt, system=None):
        captured['prompt'] = prompt
        captured['system'] = system
        return 'Polished text.'

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_text', fake_chat_text)

    result = journal.polish_journal_entry('raw text')

    assert result == 'Polished text.'
    assert captured['prompt'] == 'raw text'
    assert captured['system'] == journal._SYSTEM


def test_metadata_parses_and_caps_tags(monkeypatch):
    def fake_chat_json(prompt, system=None):
        assert prompt == 'some content'
        assert system == journal._METADATA_SYSTEM
        return {'title': 'A title', 'tags': ['work', 'health', 'family', 'goals']}

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_json', fake_chat_json)

    result = journal.generate_journal_metadata('some content')

    assert result == {'title': 'A title', 'tags': ['work', 'health', 'family']}


def test_classify_reads_yes_no_from_chat_text(monkeypatch):
    captured = {}

    def fake_chat_text(prompt, system=None):
        captured['prompt'] = prompt
        return 'yes'

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_text', fake_chat_text)

    result = journal.classify_entry_for_tag('some content', 'work')

    assert result is True
    assert 'work' in captured['prompt']
    assert 'some content' in captured['prompt']


def test_polish_skipped_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: False)
    assert journal.polish_journal_entry('raw text') == 'raw text'


def test_metadata_empty_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: False)
    assert journal.generate_journal_metadata('some content') == {}


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
