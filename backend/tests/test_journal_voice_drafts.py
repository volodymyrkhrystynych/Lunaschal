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
    """Run the background job inline instead of queueing it on the module's
    own executor, so the test can assert on what it did.

    Inline means it finishes *inside* `create_draft`, which no thread ever does
    in production — so the creation response is still the pre-processing one.
    `_settled()` is how a test sees the outcome.
    """
    def _run_now(fn):
        fn()
    monkeypatch.setattr(voice_drafts, '_run_bg', _run_now)


DEFAULT_DRAFT_ID = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


def _post_draft(client, *, draft_id=DEFAULT_DRAFT_ID,
                 filename='recording.wav', data=b'\x00' * 2048, mime='audio/wav'):
    return client.post(
        '/api/journal/voice-drafts',
        data={'id': draft_id, 'audio': (io.BytesIO(data), filename, mime)},
        content_type='multipart/form-data',
    )


def _candidates_ok(*backends):
    return [{'backend': b, 'text': f'text from {b}'} for b in backends]


def _settled(draft_id=DEFAULT_DRAFT_ID):
    """The draft as it stands once its job has run, in API shape.

    Not the same thing as the POST's 201 body, which describes the draft as
    *created*: `processing`, no entry. `create_draft` reads that row before it
    hands the clip to the worker — it has to, because both threads share one
    sqlite3 connection (see the race tests at the bottom of this file) — and on
    a real server the worker would barely have started anyway. The `_run_bg`
    fixture runs the job inline, so the work is finished by the time the test
    holds the response, but the response was taken before it. Read the row.
    """
    return voice_drafts._draft_dict(voice_drafts._load_draft(draft_id))


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
    # The 201 describes the draft as created, before any work on it.
    assert r.get_json()['status'] == 'processing'
    assert r.get_json()['entryId'] is None

    body = _settled()
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
    settled = _settled()
    second = _post_draft(client).get_json()

    # The replay is answered from the row, so by now it carries the entry the
    # first POST's own response was taken too early to see.
    assert second['id'] == first['id']
    assert second['entryId'] == settled['entryId'] is not None
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
    _post_draft(client)
    body = _settled()
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

    _post_draft(client)
    assert _settled()['status'] == 'done'
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

    _post_draft(client)
    body = _settled()
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

    _post_draft(client)
    entry = client.get(f"/api/journal/{_settled()['entryId']}").get_json()
    assert entry['rawContent'] == 'text from local'


def test_primary_falls_back_to_parakeet_when_configured_backend_did_not_succeed(client, monkeypatch):
    client.patch('/api/settings/ai', json={'sttBackend': 'openai'})  # not in DRAFT_BACKENDS
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('local'),  # parakeet itself also failed here
    )
    monkeypatch.setattr('backend.journal.voice_drafts.merge_voice_draft', lambda c, context=None: 'Merged.')

    _post_draft(client)
    entry = client.get(f"/api/journal/{_settled()['entryId']}").get_json()
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
    assert _settled()['status'] == 'done'

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


def test_deleting_a_promoted_entry_keeps_draft_history_without_a_link(
        client, monkeypatch):
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet'),
    )
    monkeypatch.setattr(
        'backend.journal.voice_drafts.merge_voice_draft',
        lambda c, context=None: 'Merged.',
    )
    created = _post_draft(client).get_json()
    entry_id = _settled()['entryId']

    response = client.delete(f"/api/journal/{entry_id}")

    assert response.status_code == 200
    assert get_db().execute(
        'SELECT * FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone() is None
    draft = get_db().execute(
        'SELECT status, entry_id FROM journal_voice_drafts WHERE id=?',
        (created['id'],),
    ).fetchone()
    assert (draft['status'], draft['entry_id']) == ('done', None)


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


# --- the shared-connection race ----------------------------------------------
#
# backend/db/connection.py keeps one sqlite3 connection for the whole process
# (`check_same_thread=False`, no lock). Two threads inside it at once make one
# of them raise `InterfaceError: bad parameter or other API misuse` and leave
# the other's cursor reset, so `fetchone()` answers None for a row that exists.
# `create_draft` used to hand the draft to the worker and *then* read the row
# back for its response, which put the route thread and the worker's own first
# read microseconds apart on every single recording — and when they collided,
# the worker believed the None and returned without a word, leaving the draft
# `processing` for ever. Two of these cost an evening each in production.

def test_the_response_row_is_read_before_the_worker_is_handed_the_draft(client, monkeypatch):
    """The route must make no DB read after submitting the job — that overlap is
    the whole race, and it is invisible to every other test here because the
    `_run_bg` fixture runs the job inline instead of on a second thread."""
    reads: list[str] = []
    real_load = voice_drafts._load_draft
    monkeypatch.setattr(
        voice_drafts, '_load_draft',
        lambda draft_id: (reads.append(draft_id), real_load(draft_id))[1],
    )

    # Counted when the handover is complete: everything the job itself read is
    # already in `reads` by then, so anything added afterwards came from the
    # route thread — which on a real server would be racing the worker.
    at_handover: list[int] = []

    def _submit(fn):
        fn()
        at_handover.append(len(reads))
    monkeypatch.setattr(voice_drafts, '_run_bg', _submit)

    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet'),
    )
    monkeypatch.setattr(
        'backend.journal.voice_drafts.merge_voice_draft',
        lambda candidates, context=None: 'Merged.',
    )

    r = _post_draft(client)
    assert r.status_code == 201
    assert at_handover, 'the background job was never submitted'
    assert len(reads) == at_handover[0]


def test_a_clobbered_read_is_retried_rather_than_believed(client, monkeypatch):
    """The worker's first read coming back None must not end the job — a second
    read settles whether the row is really gone."""
    real_load = voice_drafts._load_draft
    state = {'in_worker': False, 'dropped': False}

    def _flaky_load(draft_id):
        if state['in_worker'] and not state['dropped']:
            state['dropped'] = True
            return None
        return real_load(draft_id)
    monkeypatch.setattr(voice_drafts, '_load_draft', _flaky_load)

    def _submit(fn):
        state['in_worker'] = True
        try:
            fn()
        finally:
            state['in_worker'] = False
    monkeypatch.setattr(voice_drafts, '_run_bg', _submit)

    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet'),
    )
    monkeypatch.setattr(
        'backend.journal.voice_drafts.merge_voice_draft',
        lambda candidates, context=None: 'Merged.',
    )

    r = _post_draft(client)
    assert r.status_code == 201
    assert state['dropped'], 'the spurious None was never delivered'

    row = get_db().execute(
        'SELECT status, entry_id FROM journal_voice_drafts WHERE id=?',
        ('01ARZ3NDEKTSV4RRFFQ69G5FAV',),
    ).fetchone()
    assert row['status'] == 'done'
    assert row['entry_id'] is not None


def test_a_draft_deleted_while_queued_is_a_quiet_no_op(client, monkeypatch):
    """The innocent reason for a missing row: `delete_draft` ran while the job
    sat in the executor's queue. It must not raise and must create no entry."""
    monkeypatch.setattr(
        stt_routes, 'run_multi_backend_transcribe',
        lambda *a, **k: _candidates_ok('parakeet'),
    )
    before = get_db().execute('SELECT COUNT(*) c FROM journal_entries').fetchone()['c']

    voice_drafts._process_draft('01ARZ3NDEKTSV4RRFFQ69G5FAZ')

    after = get_db().execute('SELECT COUNT(*) c FROM journal_entries').fetchone()['c']
    assert after == before
    assert get_db().execute(
        'SELECT COUNT(*) c FROM journal_voice_drafts'
    ).fetchone()['c'] == 0
