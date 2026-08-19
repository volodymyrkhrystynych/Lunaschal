"""The multi-model STT pipeline behind the STT listener's Journal hotkey.

A clip lands as a draft, not text — several local STT backends transcribe it
in the background, and the LLM reconciles their outputs into an entry. The
behaviours worth pinning down: idempotent creation (the listener retries until
it gets an ack), no entry when every backend fails, an entry created from
whichever backends did succeed when some fail, graceful degradation to the raw
transcript when the LLM merge is unavailable, the primary-candidate/
raw_content selection rule, promotion moving the clip into normal attachment
storage, and startup crash recovery.
"""
import io
import json

import pytest

from backend.ai.journal import PolishUnavailable
from backend.db.connection import get_db, init_db
from backend.journal import voice_drafts
from backend.routes import stt as stt_routes


@pytest.fixture(autouse=True)
def _isolated_media_roots(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))
    monkeypatch.setenv('JOURNAL_DRAFTS_ROOT', str(tmp_path / 'journal-drafts'))


@pytest.fixture(autouse=True)
def _run_bg_synchronously(monkeypatch):
    """Capture the background job instead of queueing it on the module's own
    executor, so the test can run it inline and assert on what it did."""
    def _run_now(fn):
        fn()
    monkeypatch.setattr(voice_drafts, '_run_bg', _run_now)


def _post_draft(client, *, draft_id='01ARZ3NDEKTSV4RRFFQ69G5FAV',
                 filename='recording.wav', data=b'\x00' * 2048, mime='audio/wav'):
    return client.post(
        '/api/journal/voice-drafts',
        data={'id': draft_id, 'audio': (io.BytesIO(data), filename, mime)},
        content_type='multipart/form-data',
    )


def _candidates_ok(*backends):
    return [{'backend': b, 'text': f'text from {b}'} for b in backends]


# --- create / idempotency ------------------------------------------------------

def test_create_stores_the_clip_and_starts_processing(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet', 'local'),
    )
    monkeypatch.setattr(
        'backend.journal.voice_drafts.merge_voice_draft', lambda candidates, context=None: 'Merged entry text.'
    )

    r = _post_draft(client)
    assert r.status_code == 201
    body = r.get_json()
    assert body['status'] == 'done'
    assert body['entryId'] is not None

    entry = client.get(f"/api/journal/{body['entryId']}").get_json()
    assert entry['content'] == 'Merged entry text.'
    assert entry['rawContent'] == 'text from parakeet'  # parakeet is the default primary
    assert len(entry['attachments']) == 1
    assert entry['attachments'][0]['kind'] == 'audio'


def test_a_replayed_draft_is_a_no_op(client, monkeypatch):
    monkeypatch.setattr(stt_routes, 'run_multi_backend_transcribe', lambda *a, **k: _candidates_ok('parakeet'))
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')

    first = _post_draft(client).get_json()
    second = _post_draft(client).get_json()

    assert second['id'] == first['id']
    assert second['entryId'] == first['entryId']
    rows = get_db().execute('SELECT COUNT(*) AS n FROM journal_voice_drafts').fetchone()
    assert rows['n'] == 1
    entries = get_db().execute('SELECT COUNT(*) AS n FROM journal_entries').fetchone()
    assert entries['n'] == 1


def test_rejects_a_non_audio_upload(client):
    r = client.post(
        '/api/journal/voice-drafts',
        data={'id': '01ARZ3NDEKTSV4RRFFQ69G5FAW', 'audio': (io.BytesIO(b'\x00' * 64), 'photo.png', 'image/png')},
        content_type='multipart/form-data',
    )
    assert r.status_code == 400


def test_rejects_a_malformed_client_id(client):
    r = client.post(
        '/api/journal/voice-drafts',
        data={'id': 'not-a-ulid', 'audio': (io.BytesIO(b'\x00' * 64), 'r.wav', 'audio/wav')},
        content_type='multipart/form-data',
    )
    assert r.status_code == 400


# --- backend outcomes ------------------------------------------------------

def test_all_backends_failing_leaves_the_draft_errored_with_no_entry(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: [
            {'backend': 'parakeet', 'error': 'boom'},
            {'backend': 'local', 'error': 'boom'},
        ],
    )
    r = _post_draft(client)
    body = r.get_json()
    assert body['status'] == 'error'
    assert body['entryId'] is None

    entries = get_db().execute('SELECT COUNT(*) AS n FROM journal_entries').fetchone()
    assert entries['n'] == 0


def test_a_partial_failure_still_produces_an_entry(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: [
            {'backend': 'parakeet', 'text': 'good transcript'},
            {'backend': 'local', 'error': 'boom'},
        ],
    )
    captured = {}

    def fake_merge(candidates, context=None):
        captured['candidates'] = candidates
        return 'Merged from one candidate.'
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', fake_merge)

    r = _post_draft(client)
    body = r.get_json()
    assert body['status'] == 'done'
    assert len(captured['candidates']) == 1
    assert captured['candidates'][0]['backend'] == 'parakeet'


def test_merge_unavailable_falls_back_to_the_primary_raw_transcript(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet', 'local'),
    )

    def _boom(candidates, context=None):
        raise PolishUnavailable('AI is not configured')
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', _boom)

    r = _post_draft(client)
    body = r.get_json()
    assert body['status'] == 'done'

    entry = client.get(f"/api/journal/{body['entryId']}").get_json()
    assert entry['content'] == 'text from parakeet'
    assert entry['rawContent'] == 'text from parakeet'


# --- primary candidate selection --------------------------------------------

def test_primary_prefers_the_configured_default_backend(client, monkeypatch):
    client.patch('/api/settings/ai', json={'sttBackend': 'local'})
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet', 'local'),
    )
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')

    r = _post_draft(client)
    entry = client.get(f"/api/journal/{r.get_json()['entryId']}").get_json()
    assert entry['rawContent'] == 'text from local'


def test_primary_falls_back_to_parakeet_when_configured_backend_did_not_succeed(client, monkeypatch):
    client.patch('/api/settings/ai', json={'sttBackend': 'openai'})  # not in DRAFT_BACKENDS
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('local'),  # parakeet itself also failed here
    )
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')

    r = _post_draft(client)
    entry = client.get(f"/api/journal/{r.get_json()['entryId']}").get_json()
    # Neither the configured backend nor parakeet succeeded — falls back to
    # the first candidate rather than raising.
    assert entry['rawContent'] == 'text from local'


# --- list / delete / retry --------------------------------------------------

def test_done_drafts_do_not_appear_in_the_list(client, monkeypatch):
    monkeypatch.setattr(stt_routes, 'run_multi_backend_transcribe', lambda *a, **k: _candidates_ok('parakeet'))
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')
    _post_draft(client)

    listed = client.get('/api/journal/voice-drafts').get_json()
    assert listed == []


def test_errored_drafts_appear_in_the_list_and_can_be_retried(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: [{'backend': 'parakeet', 'error': 'boom'}],
    )
    created = _post_draft(client).get_json()
    listed = client.get('/api/journal/voice-drafts').get_json()
    assert [d['id'] for d in listed] == [created['id']]
    assert listed[0]['status'] == 'error'

    monkeypatch.setattr(stt_routes, 'run_multi_backend_transcribe', lambda *a, **k: _candidates_ok('parakeet'))
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')
    retry = client.post(f"/api/journal/voice-drafts/{created['id']}/retry")
    assert retry.status_code == 200

    listed_after = client.get('/api/journal/voice-drafts').get_json()
    assert listed_after == []  # now done, so it drops off the list


def test_retry_404s_for_a_draft_that_is_not_in_error(client, monkeypatch):
    monkeypatch.setattr(stt_routes, 'run_multi_backend_transcribe', lambda *a, **k: _candidates_ok('parakeet'))
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')
    created = _post_draft(client).get_json()
    assert created['status'] == 'done'

    r = client.post(f"/api/journal/voice-drafts/{created['id']}/retry")
    assert r.status_code == 404


def test_retry_404s_for_an_unknown_draft(client):
    r = client.post('/api/journal/voice-drafts/does-not-exist/retry')
    assert r.status_code == 404


def test_delete_removes_an_unpromoted_draft(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: [{'backend': 'parakeet', 'error': 'boom'}],
    )
    created = _post_draft(client).get_json()
    r = client.delete(f"/api/journal/voice-drafts/{created['id']}")
    assert r.status_code == 200
    assert get_db().execute('SELECT * FROM journal_voice_drafts').fetchone() is None


def test_delete_refuses_a_promoted_draft(client, monkeypatch):
    monkeypatch.setattr(stt_routes, 'run_multi_backend_transcribe', lambda *a, **k: _candidates_ok('parakeet'))
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')
    created = _post_draft(client).get_json()

    r = client.delete(f"/api/journal/voice-drafts/{created['id']}")
    assert r.status_code == 404
    assert get_db().execute('SELECT * FROM journal_voice_drafts').fetchone() is not None


def test_draft_audio_is_playable_while_pending(client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: [{'backend': 'parakeet', 'error': 'boom'}],
    )
    created = _post_draft(client).get_json()
    r = client.get(created['url'])
    assert r.status_code == 200
    assert r.data == b'\x00' * 2048


# --- startup crash recovery --------------------------------------------------

def test_a_processing_row_is_reset_to_error_on_startup(client):
    db = get_db()
    db.execute(
        "INSERT INTO journal_voice_drafts(id, path, mime, size, status, created_at)"
        " VALUES ('01ARZ3NDEKTSV4RRFFQ69G5FAX', '/tmp/x.wav', 'audio/wav', 10, 'processing', 0)"
    )
    db.commit()

    init_db()

    row = db.execute(
        "SELECT status, error FROM journal_voice_drafts WHERE id='01ARZ3NDEKTSV4RRFFQ69G5FAX'"
    ).fetchone()
    assert row['status'] == 'error'
    assert 'restart' in row['error']
