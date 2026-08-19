"""Unit tests for backend.ai.transcribe_polish — the light cleanup pass
POST /api/transcribe runs on every dictation. Fakes chat_text directly; the
native transport is covered by test_llm.py."""
from backend.ai import transcribe_polish


def test_polish_passes_system_prompt_and_returns_cleaned_text(monkeypatch):
    captured = {}

    def fake_chat_text(prompt, system=None):
        captured['prompt'] = prompt
        captured['system'] = system
        return 'Polished text.'

    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(transcribe_polish, 'chat_text', fake_chat_text)

    result = transcribe_polish.polish_transcript('raw text')

    assert result == 'Polished text.'
    assert captured['prompt'] == 'raw text'
    assert captured['system'] == transcribe_polish._SYSTEM


def test_returns_raw_text_unchanged_when_ai_not_configured(monkeypatch):
    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: False)
    assert transcribe_polish.polish_transcript('raw text') == 'raw text'


def test_returns_raw_text_unchanged_on_empty_input(monkeypatch):
    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)
    assert transcribe_polish.polish_transcript('') == ''
    assert transcribe_polish.polish_transcript('   ') == ''


def test_falls_back_to_raw_text_when_the_call_fails(monkeypatch):
    def fake_chat_text(prompt, system=None):
        raise RuntimeError('llama-server unreachable')

    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(transcribe_polish, 'chat_text', fake_chat_text)

    assert transcribe_polish.polish_transcript('raw text') == 'raw text'


def test_falls_back_to_raw_text_when_the_model_returns_nothing_usable(monkeypatch):
    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(transcribe_polish, 'chat_text', lambda prompt, system=None: '   ')

    assert transcribe_polish.polish_transcript('raw text') == 'raw text'


def test_strips_preamble_and_wrapping_quotes(monkeypatch):
    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        transcribe_polish, 'chat_text',
        lambda prompt, system=None: 'Here is the corrected transcript:\n"Hello, world."',
    )

    assert transcribe_polish.polish_transcript('hello world') == 'Hello, world.'
