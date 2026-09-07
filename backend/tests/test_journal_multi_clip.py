"""Several clips, one entry.

A composer stages clips and sends them together, so `POST /api/journal/recordings`
is now called more than once against the same entry id. What is pinned here is
what that produces: the transcripts appended in the order the clips were spoken,
separated by a blank line, and the entry's downstream passes reading the whole
body rather than whichever clip happened to finish first.

The rule that makes it safe is that a clip contributes to the entry exactly
once. Re-running Transcribe on a clip refreshes that clip's own text and does
not paste it into the entry a second time.
"""
import io

import pytest
from ulid import ULID

from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))
    from backend.journal import storage

    monkeypatch.setattr(storage, '_root_override', None, raising=False)
    yield


def _run_pending_bg(monkeypatch):
    """Hold queued jobs back so a test can run them in a chosen order."""
    from backend.ai import job_handlers  # noqa: F401  (registers handlers)
    from backend.ai import jobs as llm_jobs

    captured = []
    real = llm_jobs.enqueue

    def capture(kind, target_id=None, payload=None, *, commit=True):
        job_id = real(kind, target_id, payload, commit=commit)
        if job_id is not None:
            captured.append((kind, lambda jid=job_id: llm_jobs.process_one(jid)))
        return job_id

    monkeypatch.setattr(llm_jobs, 'enqueue', capture)
    return captured


def _clip(client, entry_id, attachment_id, *, name='Recording'):
    return client.post(
        '/api/journal/recordings',
        data={
            'file': (io.BytesIO(b'\x00' * 2048), 'recording.webm', 'audio/webm'),
            'id': entry_id,
            'attachmentId': attachment_id,
            'transcribe': 'true',
            'name': name,
        },
        content_type='multipart/form-data',
    )


def _transcribes(monkeypatch, by_path=None, text='said something'):
    """Stub speech-to-text. `by_path` gives each stored file its own text, so a
    test can tell the clips apart."""
    def _run(path):
        if by_path is None:
            return text
        return by_path[str(path)]

    monkeypatch.setattr(journal_routes, '_do_attachment_audio', _run)
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(
        journal_routes, '_generate_metadata_bg', lambda *a, **k: None
    )


def _stored_path(attachment_id):
    """Where a clip actually landed, so a stub can give each its own words."""
    from backend.db.connection import get_db
    from backend.journal import storage

    row = get_db().execute(
        'SELECT path FROM journal_attachments WHERE id=?', (attachment_id,)
    ).fetchone()
    return str(storage.resolve_stored_path(row['path']))


def _run_kind(jobs, kind):
    for job_kind, run in list(jobs):
        if job_kind == kind:
            run()


def test_two_clips_append_in_order_with_a_blank_line_between_them(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    entry_id, a1, a2 = str(ULID()), str(ULID()), str(ULID())

    r1 = _clip(client, entry_id, a1)
    r2 = _clip(client, entry_id, a2)
    assert (r1.status_code, r2.status_code) == (201, 201)
    # One entry, not two: the second upload finds the id already there.
    assert r1.get_json()['id'] == r2.get_json()['id'] == entry_id

    _transcribes(
        monkeypatch,
        by_path={
            _stored_path(a1): 'First thought.',
            _stored_path(a2): 'Second thought.',
        },
    )

    _run_kind(jobs, 'journal.transcribe_attachment')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    # The gap is the pause that was taken; a space would read as a run-on.
    assert entry['rawContent'] == 'First thought.\n\nSecond thought.'
    assert entry['content'] == 'First thought.\n\nSecond thought.'
    # Each clip keeps its own transcript as well, which is what the collapsible
    # block under the player shows.
    assert [a['transcript'] for a in entry['attachments']] == [
        'First thought.',
        'Second thought.',
    ]


def test_re_transcribing_a_clip_does_not_paste_it_into_the_entry_twice(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    entry_id, att_id = str(ULID()), str(ULID())
    _clip(client, entry_id, att_id)
    _transcribes(monkeypatch, text='Only once.')
    _run_kind(jobs, 'journal.transcribe_attachment')
    assert (
        client.get(f'/api/journal/{entry_id}').get_json()['rawContent']
        == 'Only once.'
    )

    jobs.clear()
    _transcribes(monkeypatch, text='A better reading.')
    assert client.post(
        f'/api/journal/attachments/{att_id}/transcribe'
    ).status_code == 202
    _run_kind(jobs, 'journal.transcribe_attachment')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    # The clip's own text is refreshed...
    assert entry['attachments'][0]['transcript'] == 'A better reading.'
    # ...and the entry is left exactly as it was. Re-running the button is a
    # correction to the clip, not a second contribution to the body.
    assert entry['rawContent'] == 'Only once.'


def test_polish_and_titling_see_the_whole_body_not_one_clip(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    polished, titled = [], []
    monkeypatch.setattr(
        journal_routes, '_polish_bg', lambda _id, text, **k: polished.append(text)
    )
    monkeypatch.setattr(
        journal_routes,
        '_generate_metadata_bg',
        lambda _id, text, **k: titled.append(text),
    )
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio', lambda _p: 'a clip'
    )

    entry_id = str(ULID())
    _clip(client, entry_id, str(ULID()))
    _clip(client, entry_id, str(ULID()))
    _run_kind(jobs, 'journal.transcribe_attachment')

    # Run once per clip, and the *last* run — the one that writes last, since
    # these share one FIFO worker — saw both.
    assert polished[-1] == 'a clip\n\na clip'
    assert titled[-1] == 'a clip\n\na clip'


def test_a_clip_appends_after_the_words_that_were_typed_with_it(
    client, monkeypatch
):
    """The composer sends the typed text as the create and the clips after it.
    The transcript joins the end of what was written rather than replacing it."""
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch, text='and the bit I forgot to write down')

    entry_id = str(ULID())
    assert client.post(
        '/api/journal', json={'id': entry_id, 'content': 'Typed first.'}
    ).status_code == 201
    _clip(client, entry_id, str(ULID()))
    _run_kind(jobs, 'journal.transcribe_attachment')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['rawContent'] == (
        'Typed first.\n\nand the bit I forgot to write down'
    )


def test_a_failed_clip_leaves_the_body_and_the_other_clips_alone(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    entry_id, good, bad = str(ULID()), str(ULID()), str(ULID())
    _clip(client, entry_id, good)
    _clip(client, entry_id, bad)

    bad_path = _stored_path(bad)

    def _run(path):
        if str(path) == bad_path:
            raise RuntimeError('No speech found in the recording')
        return 'the one that worked'

    monkeypatch.setattr(journal_routes, '_do_attachment_audio', _run)
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(
        journal_routes, '_generate_metadata_bg', lambda *a, **k: None
    )
    _run_kind(jobs, 'journal.transcribe_attachment')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['rawContent'] == 'the one that worked'
    statuses = {a['id']: a['transcriptStatus'] for a in entry['attachments']}
    assert statuses[good] == 'done'
    assert statuses[bad] == 'error'
    # The audio of the failed one is still there to try again with.
    assert client.get(f'/api/journal/attachments/{bad}/file').status_code == 200


def test_a_clip_that_lands_before_the_create_does_not_swallow_the_typed_text(
    client, monkeypatch
):
    """The composer mints its entry id at the first chunk, so the two halves can
    arrive in either order — the boot sweep after a crash sends only the clip,
    and an offline queue replays in whatever order it can. The create used to be
    a plain INSERT OR IGNORE, which meant the words typed beside the audio were
    silently dropped whenever the clip got there first."""
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch, text='and what I said about it')
    entry_id = str(ULID())

    # Clip first: the recording route creates the entry, empty.
    _clip(client, entry_id, str(ULID()))
    assert client.get(f'/api/journal/{entry_id}').get_json()['content'] == ''

    # Then the create it was recorded alongside.
    assert client.post(
        '/api/journal', json={'id': entry_id, 'content': 'What I typed.'}
    ).status_code == 201
    _run_kind(jobs, 'journal.transcribe_attachment')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['rawContent'] == 'What I typed.\n\nand what I said about it'


def test_a_replayed_create_still_leaves_a_real_entry_alone(client):
    """The fill-in above must only reach a row that is still empty — a create
    replayed by the offline queue against an entry that already has its text is
    an ordinary duplicate."""
    entry_id = str(ULID())
    first = client.post(
        '/api/journal', json={'id': entry_id, 'content': 'The real one.'}
    )
    second = client.post(
        '/api/journal', json={'id': entry_id, 'content': 'A replay of it.'}
    )
    assert (first.status_code, second.status_code) == (201, 201)
    assert (
        client.get(f'/api/journal/{entry_id}').get_json()['content']
        == 'The real one.'
    )
