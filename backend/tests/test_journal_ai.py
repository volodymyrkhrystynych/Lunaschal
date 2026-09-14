"""Unit tests for `backend.ai.journal`'s three LLM call sites — confirms each
calls the shared chat_json/chat_text helpers (backend.ai.llm) with the right
system prompt and processes the result correctly. The native transport itself
is covered by test_llm.py; these tests fake chat_json/chat_text directly."""
import pytest

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


def test_polish_appends_attachment_context_when_given(monkeypatch):
    captured = {}

    def fake_chat_text(prompt, system=None):
        captured['prompt'] = prompt
        return 'Polished text.'

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_text', fake_chat_text)

    journal.polish_journal_entry(
        'raw text', context='backyard recording: A dog barks twice.'
    )

    assert captured['prompt'] == (
        'raw text\n\n---\nContext:\nbackyard recording: A dog barks twice.'
    )


def test_polish_omits_context_block_when_none_given(monkeypatch):
    captured = {}

    def fake_chat_text(prompt, system=None):
        captured['prompt'] = prompt
        return 'Polished text.'

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_text', fake_chat_text)

    journal.polish_journal_entry('raw text')

    assert captured['prompt'] == 'raw text'


def test_metadata_parses_and_caps_tags(monkeypatch):
    def fake_chat_json(prompt, system=None, **kwargs):
        assert prompt == 'some content'
        assert system == journal._METADATA_SYSTEM
        return {'title': 'A title', 'tags': ['work', 'health', 'family', 'goals']}

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_json', fake_chat_json)

    result = journal.generate_journal_metadata('some content')

    assert result == {'title': 'A title', 'tags': ['work', 'health', 'family']}


def _capture_metadata_prompt(monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, **kwargs):
        captured['prompt'] = prompt
        return {'title': 'A title', 'tags': ['memory']}

    monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal, 'chat_json', fake_chat_json)
    return captured


def test_metadata_sees_the_photo_captions(monkeypatch):
    """The point of the whole change. "Look at this" plus a photo used to title
    an entry about nothing, because nothing about the picture reached this call.
    """
    captured = _capture_metadata_prompt(monkeypatch)

    journal.generate_journal_metadata(
        'Look at this.',
        'Photo: A grey brick building numbered 28 beside a sign reading ADV METAL.',
    )

    assert 'Look at this.' in captured['prompt']
    assert 'ADV METAL' in captured['prompt']


def test_metadata_sees_what_a_watched_video_was_about(monkeypatch):
    """The same failure one step over: "watched this" plus a link titled an
    entry about nothing, while the import pipeline had already worked out
    exactly what the video said."""
    captured = _capture_metadata_prompt(monkeypatch)

    journal.generate_journal_metadata(
        'Watched this on the train.',
        'Video "But what is a neural network?" — It builds up to backpropagation.',
    )

    assert 'Watched this on the train.' in captured['prompt']
    assert 'backpropagation' in captured['prompt']


def test_the_context_block_carries_no_heading_of_its_own(monkeypatch):
    """It used to be introduced as "Attached photos:", which mislabels a video.
    The lines label themselves and the system prompt says what they mean."""
    captured = _capture_metadata_prompt(monkeypatch)

    journal.generate_journal_metadata('Watched this.', 'Video "A talk" — Ants.')

    assert 'Attached photos' not in captured['prompt']
    assert captured['prompt'] == 'Watched this.\n\n---\nVideo "A talk" — Ants.'


def test_metadata_without_context_sends_the_content_alone(monkeypatch):
    """No stray heading when there are no photos — every existing entry goes
    through this path."""
    captured = _capture_metadata_prompt(monkeypatch)

    journal.generate_journal_metadata('A quiet day.')

    assert captured['prompt'] == 'A quiet day.'


def test_a_photo_only_entry_still_gets_a_title(monkeypatch):
    """Empty text used to short-circuit before the model was asked. A photo with
    no words is a real entry, and it is exactly the one that needs a title."""
    captured = _capture_metadata_prompt(monkeypatch)

    result = journal.generate_journal_metadata('', 'Photo: A cat asleep on a radiator.')

    assert result['title'] == 'A title'
    assert 'radiator' in captured['prompt']


def test_an_entirely_empty_entry_asks_nothing(monkeypatch):
    captured = _capture_metadata_prompt(monkeypatch)

    assert journal.generate_journal_metadata('   ', '  ') == {}
    assert 'prompt' not in captured


class TestFicCommentaryMetadata:
    """generate_fic_commentary_metadata is generate_journal_metadata's
    counterpart for the fanfic reader's commentary entries: the fic's
    description and the chapter's own text stand in for the photo captions a
    regular entry gets."""

    def _capture(self, monkeypatch):
        captured = {}

        def fake_chat_json(prompt, system=None, **kwargs):
            captured['prompt'] = prompt
            captured['system'] = system
            return {'title': 'A title', 'tags': ['reading']}

        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'chat_json', fake_chat_json)
        return captured

    def test_uses_the_dedicated_system_prompt(self, monkeypatch):
        captured = self._capture(monkeypatch)

        journal.generate_fic_commentary_metadata(
            'loved this twist!!', fic_title='Worm Redux',
        )

        assert captured['system'] == journal._FIC_COMMENTARY_METADATA_SYSTEM

    def test_folds_fic_description_and_chapter_text_into_the_prompt(self, monkeypatch):
        captured = self._capture(monkeypatch)

        journal.generate_fic_commentary_metadata(
            'loved this twist!!',
            fic_title='Worm Redux',
            fic_description='A girl gets superpowers.',
            chapter_title='Chapter 12',
            chapter_text='Taylor revealed her identity to the team.',
        )

        assert 'loved this twist!!' in captured['prompt']
        assert 'Worm Redux' in captured['prompt']
        assert 'A girl gets superpowers.' in captured['prompt']
        assert 'Chapter 12' in captured['prompt']
        assert 'Taylor revealed her identity to the team.' in captured['prompt']

    def test_truncates_a_long_chapter(self, monkeypatch):
        captured = self._capture(monkeypatch)

        journal.generate_fic_commentary_metadata(
            'so good', fic_title='Worm Redux', chapter_text='x' * 5000,
        )

        assert len(captured['prompt']) < 5000

    def test_omits_optional_context_lines_when_absent(self, monkeypatch):
        captured = self._capture(monkeypatch)

        journal.generate_fic_commentary_metadata('so good', fic_title='Worm Redux')

        assert captured['prompt'] == 'so good\n\n---\nFic: Worm Redux'

    def test_caps_and_returns_tags(self, monkeypatch):
        def fake_chat_json(prompt, system=None, **kwargs):
            return {'title': 'A title', 'tags': ['reading', 'creative', 'memory', 'work']}

        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'chat_json', fake_chat_json)

        result = journal.generate_fic_commentary_metadata('so good', fic_title='Worm Redux')

        assert result == {'title': 'A title', 'tags': ['reading', 'creative', 'memory']}

    def test_empty_when_ai_unconfigured(self, monkeypatch):
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: False)
        assert journal.generate_fic_commentary_metadata(
            'so good', fic_title='Worm Redux',
        ) == {}

    def test_empty_commentary_asks_nothing(self, monkeypatch):
        captured = self._capture(monkeypatch)

        assert journal.generate_fic_commentary_metadata(
            '   ', fic_title='Worm Redux',
        ) == {}
        assert 'prompt' not in captured


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


def test_polish_raises_when_ai_unconfigured(monkeypatch):
    """It used to return the raw text here, which the caller could not tell
    apart from a successful polish — see test_journal_polish.py."""
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: False)
    with pytest.raises(journal.PolishUnavailable):
        journal.polish_journal_entry('raw text')


def test_metadata_empty_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(journal, 'is_ai_configured', lambda: False)
    assert journal.generate_journal_metadata('some content') == {}


class TestMergeVoiceDraft:
    """merge_voice_draft is polish_journal_entry's multi-candidate counterpart
    — backend/journal/voice_drafts.py's draft pipeline calls it with 1-3 STT
    outputs of the same clip instead of one."""

    def test_labels_each_candidate_and_passes_merge_system_prompt(self, monkeypatch):
        captured = {}

        def fake_chat_text(prompt, system=None):
            captured['prompt'] = prompt
            captured['system'] = system
            return 'Merged text.'

        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'chat_text', fake_chat_text)

        result = journal.merge_voice_draft([
            {'backend': 'parakeet', 'text': 'hello world'},
            {'backend': 'local', 'text': 'hallo world'},
        ])

        assert result == 'Merged text.'
        assert captured['system'] == journal._MERGE_SYSTEM
        assert 'Transcript A (parakeet):\nhello world' in captured['prompt']
        assert 'Transcript B (local):\nhallo world' in captured['prompt']

    def test_appends_context_block_when_given(self, monkeypatch):
        captured = {}

        def fake_chat_text(prompt, system=None):
            captured['prompt'] = prompt
            return 'Merged text.'

        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'chat_text', fake_chat_text)

        journal.merge_voice_draft(
            [{'backend': 'parakeet', 'text': 'hello world'}],
            context='Things already known about the user:\nTheir dog is named Wren.',
        )

        assert captured['prompt'].endswith(
            '\n\n---\nContext:\nThings already known about the user:\nTheir dog is named Wren.'
        )

    def test_works_with_a_single_candidate(self, monkeypatch):
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'chat_text', lambda prompt, system=None: 'Merged text.')

        result = journal.merge_voice_draft([{'backend': 'parakeet', 'text': 'hello world'}])
        assert result == 'Merged text.'

    def test_raises_when_no_candidates_have_text(self, monkeypatch):
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        with pytest.raises(journal.PolishUnavailable, match='no candidate'):
            journal.merge_voice_draft([{'backend': 'parakeet', 'text': ''}, {'backend': 'local', 'error': 'boom'}])

    def test_raises_when_ai_unconfigured(self, monkeypatch):
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: False)
        with pytest.raises(journal.PolishUnavailable):
            journal.merge_voice_draft([{'backend': 'parakeet', 'text': 'hello world'}])

    def test_raises_when_ai_unreachable(self, monkeypatch):
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        def _boom(*a, **k):
            raise RuntimeError('Connection error.')
        monkeypatch.setattr(journal, 'chat_text', _boom)

        with pytest.raises(journal.PolishUnavailable, match='Connection error'):
            journal.merge_voice_draft([{'backend': 'parakeet', 'text': 'hello world'}])

    def test_raises_on_empty_completion(self, monkeypatch):
        monkeypatch.setattr(journal, 'is_ai_configured', lambda: True)
        monkeypatch.setattr(journal, 'chat_text', lambda prompt, system=None: '   ')

        with pytest.raises(journal.PolishUnavailable, match='empty'):
            journal.merge_voice_draft([{'backend': 'parakeet', 'text': 'hello world'}])


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
