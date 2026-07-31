"""A failed polish must not look like a successful one.

`polish_journal_entry` used to swallow every exception and return the raw text,
which the route could not tell apart from a real polish — so with llama-server
down, clicking Polish overwrote an already-polished entry with its raw
transcript and returned 200. The button looked broken; the entry silently lost
its polish.
"""
import pytest

from backend.ai import journal as journal_ai
from backend.ai.journal import PolishUnavailable, polish_journal_entry
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _no_metadata_thread(monkeypatch):
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg', lambda *a, **k: None)


def _entry_with_raw(client, monkeypatch, raw='so today was rough i barely slept'):
    """Create an entry that has a raw transcript, without the polish thread
    racing the test for the same row."""
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    r = client.post('/api/journal', json={'raw_content': raw})
    assert r.status_code == 201
    return r.get_json()['id']


# --- backend/ai/journal.py ---------------------------------------------------

def test_polish_raises_when_ai_unreachable(monkeypatch):
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: True)
    def _boom(*a, **k):
        raise RuntimeError('Connection error.')
    monkeypatch.setattr(journal_ai, 'chat_text', _boom)

    with pytest.raises(PolishUnavailable, match='Connection error'):
        polish_journal_entry('some raw text')


def test_polish_raises_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: False)
    with pytest.raises(PolishUnavailable):
        polish_journal_entry('some raw text')


def test_polish_raises_on_empty_completion(monkeypatch):
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal_ai, 'chat_text', lambda *a, **k: '   ')
    with pytest.raises(PolishUnavailable):
        polish_journal_entry('some raw text')


def test_polish_returns_cleaned_text(monkeypatch):
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        journal_ai, 'chat_text',
        lambda *a, **k: 'Here is the corrected text:\n"So today was rough."',
    )
    assert polish_journal_entry('so today was rough') == 'So today was rough.'


def test_blank_input_is_returned_untouched(monkeypatch):
    # No AI call at all, so no failure to report.
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: False)
    assert polish_journal_entry('   ') == '   '


# --- POST /api/journal/<id>/polish -------------------------------------------

def test_route_reports_503_and_keeps_content_when_ai_is_down(client, monkeypatch):
    entry_id = _entry_with_raw(client, monkeypatch, raw='raw dictation here')
    # Simulate a previous successful polish, so there is something to lose.
    client.patch(f'/api/journal/{entry_id}', json={'content': 'Polished prose.'})

    def _unavailable(_text):
        raise PolishUnavailable('Connection error.')
    monkeypatch.setattr(journal_routes, 'polish_journal_entry', _unavailable)

    r = client.post(f'/api/journal/{entry_id}/polish')
    assert r.status_code == 503
    assert 'Connection error' in r.get_json()['error']

    # The whole point: the earlier polish survived the failed re-polish.
    assert client.get(f'/api/journal/{entry_id}').get_json()['content'] == 'Polished prose.'


def test_route_writes_the_polish_on_success(client, monkeypatch):
    entry_id = _entry_with_raw(client, monkeypatch, raw='raw dictation here')
    monkeypatch.setattr(
        journal_routes, 'polish_journal_entry', lambda _t: 'Raw dictation here.'
    )

    r = client.post(f'/api/journal/{entry_id}/polish')
    assert r.status_code == 200
    assert r.get_json()['content'] == 'Raw dictation here.'
    assert client.get(f'/api/journal/{entry_id}').get_json()['content'] == 'Raw dictation here.'


def test_route_400s_without_a_transcript_to_polish(client, monkeypatch):
    created = client.post('/api/journal', json={'content': 'Typed by hand.'}).get_json()
    r = client.post(f"/api/journal/{created['id']}/polish")
    assert r.status_code == 400
