"""Recording still works while the GPU is off.

This is the requirement the switch was asked for with, and the one most likely
to rot: transcription is CPU work (parakeet is the default backend and costs no
VRAM at all), so a paused card must not stop a clip becoming an entry. What it
may stop is the *polish* — and that has to be queued rather than dropped, or
"paused" quietly means "lost" for everything dictated during an evening.

The pieces that make this work already existed and were never pinned down:
`backend/ai/journal.py` turns every model failure into `PolishUnavailable`, and
the draft pipeline already saved raw text on that. These tests hold both in
place, and add the queued re-polish that closes the gap.
"""
import io

import pytest

from backend.ai import jobs, service
from backend.db.connection import get_db
from backend.journal import voice_drafts
from backend.routes import stt as stt_routes


@pytest.fixture(autouse=True)
def _isolated_media_roots(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))
    monkeypatch.setenv('JOURNAL_DRAFTS_ROOT', str(tmp_path / 'journal-drafts'))


@pytest.fixture(autouse=True)
def _inline_draft_worker(monkeypatch):
    monkeypatch.setattr(voice_drafts, '_run_bg', lambda fn: fn())


@pytest.fixture(autouse=True)
def _clean_service(client):
    service.reset()
    yield
    service.reset()


@pytest.fixture
def paused(client, monkeypatch):
    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, '_router_post', lambda *a, **k: (True, None))
    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()
    return client


def _transcribes(monkeypatch, *backends):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: [{'backend': b, 'text': f'text from {b}'} for b in backends],
    )


def _post_draft(client, draft_id='01ARZ3NDEKTSV4RRFFQ69G5FAV'):
    return client.post(
        '/api/journal/voice-drafts',
        data={'id': draft_id, 'audio': (io.BytesIO(b'\x00' * 2048),
                                        'recording.wav', 'audio/wav')},
        content_type='multipart/form-data',
    )


def test_a_clip_recorded_while_paused_still_becomes_an_entry(paused, monkeypatch):
    _transcribes(monkeypatch, 'parakeet', 'local')

    body = _post_draft(paused).get_json()

    assert body['status'] == 'done'
    assert body['entryId'] is not None
    entry = paused.get(f"/api/journal/{body['entryId']}").get_json()
    # Unpolished, because the merge is a GPU call and the GPU is off — but the
    # transcript is there, which is the thing that cannot be recovered later.
    assert entry['rawContent'] == 'text from parakeet'
    assert entry['content'] == 'text from parakeet'


def test_the_missed_polish_is_queued_rather_than_dropped(paused, monkeypatch):
    _transcribes(monkeypatch, 'parakeet')

    entry_id = _post_draft(paused).get_json()['entryId']

    row = get_db().execute(
        "SELECT * FROM llm_jobs WHERE kind='journal.polish' AND target_id=?",
        (entry_id,),
    ).fetchone()
    assert row is not None, 'a paused polish must be queued, not lost'
    assert row['status'] == 'pending'


def test_nothing_is_queued_when_the_merge_actually_ran(client, monkeypatch):
    _transcribes(monkeypatch, 'parakeet')
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft',
                        lambda c, context=None: 'Merged entry text.')

    entry_id = _post_draft(client).get_json()['entryId']

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['content'] == 'Merged entry text.'
    assert get_db().execute(
        "SELECT COUNT(*) AS n FROM llm_jobs WHERE kind='journal.polish'"
        ' AND target_id=?', (entry_id,)).fetchone()['n'] == 0


def test_the_queued_polish_runs_on_resume(paused, monkeypatch):
    _transcribes(monkeypatch, 'parakeet')
    entry_id = _post_draft(paused).get_json()['entryId']

    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, '_router_post', lambda *a, **k: (True, None))
    paused.post('/api/settings/inference/resume')
    service.invalidate_pause_cache()

    monkeypatch.setattr('backend.routes.journal.polish_journal_entry',
                        lambda raw, context=None: 'Polished at last.')
    assert jobs.drain_once() is not None

    entry = paused.get(f'/api/journal/{entry_id}').get_json()
    assert entry['content'] == 'Polished at last.'


def test_transcript_cleanup_degrades_to_the_raw_text(client, monkeypatch):
    """`/api/transcribe`'s merge is a GPU call; the transcript is not."""
    from backend.ai import transcribe_polish

    def _paused(*a, **k):
        raise service.InferencePaused('GPU inference is paused')

    monkeypatch.setattr(transcribe_polish, 'chat_text', _paused)
    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)

    out = transcribe_polish.merge_transcripts(['the raw words', 'the raw words too'])

    # Not an exception and not an empty string: every dictation surface depends
    # on this returning text.
    assert out == 'the raw words'
