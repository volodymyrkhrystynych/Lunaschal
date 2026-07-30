"""Unit tests for `backend.ai.writing.summarize_discussion` — confirms it
calls the shared chat_json helper (backend.ai.llm) with the right system
prompt and processes the result correctly. No real LLM calls."""
from backend.ai import writing
from backend.ai.llm import EmptyCompletion


def _configure_ai(monkeypatch):
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: True)


def test_summarize_discussion_parses_result(monkeypatch):
    captured = {}

    def fake_chat_json(transcript, system=None, **kwargs):
        captured['transcript'] = transcript
        captured['system'] = system
        return {'title': 'Villain twist', 'content': '- Brother is the villain'}

    _configure_ai(monkeypatch)
    monkeypatch.setattr(writing, 'chat_json', fake_chat_json)

    result = writing.summarize_discussion(
        'Author: what if...\n\nAssistant: yes', 'My Story', 'A tale'
    )

    assert result == {'title': 'Villain twist', 'content': '- Brother is the villain'}
    assert 'My Story' in captured['system']
    assert 'A tale' in captured['system']
    assert captured['transcript'] == 'Author: what if...\n\nAssistant: yes'


def test_summarize_discussion_truncates_keeping_tail(monkeypatch):
    captured = {}

    def fake_chat_json(transcript, system=None, **kwargs):
        captured['transcript'] = transcript
        return {'title': 'T', 'content': 'C'}

    _configure_ai(monkeypatch)
    monkeypatch.setattr(writing, 'chat_json', fake_chat_json)

    transcript = 'OLD ' * 10000 + 'RECENT DECISION'
    writing.summarize_discussion(transcript, 'My Story')

    assert len(captured['transcript']) == writing._MAX_INPUT_CHARS
    assert captured['transcript'].endswith('RECENT DECISION')


def test_summarize_discussion_malformed_json(monkeypatch):
    def fake_chat_json(transcript, system=None, **kwargs):
        raise EmptyCompletion('model returned non-JSON content')

    _configure_ai(monkeypatch)
    monkeypatch.setattr(writing, 'chat_json', fake_chat_json)

    assert writing.summarize_discussion('Author: hi', 'My Story') is None


def test_summarize_discussion_missing_fields(monkeypatch):
    _configure_ai(monkeypatch)
    monkeypatch.setattr(writing, 'chat_json', lambda t, system=None: {'title': 'Only a title'})

    assert writing.summarize_discussion('Author: hi', 'My Story') is None


def test_summarize_discussion_empty_transcript(monkeypatch):
    _configure_ai(monkeypatch)
    assert writing.summarize_discussion('   ', 'My Story') is None


def test_summarize_discussion_unconfigured(monkeypatch):
    monkeypatch.setattr(writing, 'is_ai_configured', lambda: False)
    assert writing.summarize_discussion('Author: hi', 'My Story') is None
