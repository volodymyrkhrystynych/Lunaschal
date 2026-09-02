"""Commentary recorded in the fanfic reader: one clip, an entry, a chapter link.

The reader's Commentary microphone uploads through the journal's recording
endpoint with a `ficId` (and, for a fic that has chapters, a `chapterId`), so
the audio, the journal entry and the row that says which chapter it was about
are created by a single request that stays idempotent under replay. The
transcript arrives later and is written into the entry.

The link rides along with the upload rather than following in a second
`POST /api/fanfic/<id>/journal-link` call precisely because the upload is
replayed until it lands: a separate call is the one step in the sequence a
dropped connection can lose, and what it loses is the only thing saying the
entry is about a chapter at all. So what is pinned down here is the linkage and
its failure modes — a replayed upload, a rejected file, and ids that name
nothing.
"""
import io
import time

import pytest
from ulid import ULID

from backend.db import connection
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))
    from backend.journal import storage

    monkeypatch.setattr(storage, '_root_override', None, raising=False)
    yield


def _run_pending_bg(monkeypatch):
    """Capture background jobs instead of queueing them, so a test can run them
    synchronously and assert on what they wrote."""
    jobs = []
    monkeypatch.setattr(journal_routes, 'run_bg', jobs.append)
    return jobs


def _fic(title='Test Fic', *, chapters=1):
    """A downloaded fic with chapters, straight into the tables — the real path
    is a forum scrape."""
    db = connection.get_db()
    now = int(time.time())
    fic_id = str(ULID())
    db.execute(
        'INSERT INTO fics(id, title, source_type, created_at, updated_at)'
        " VALUES (?,?,'xenforo',?,?)",
        (fic_id, title, now, now),
    )
    chapter_ids = []
    for position in range(1, chapters + 1):
        chapter_id = str(ULID())
        db.execute(
            'INSERT INTO fic_chapters(id, fic_id, position, title, content_html,'
            ' content_text, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (chapter_id, fic_id, position, f'Chapter {position}',
             '<p>text</p>', 'text', now, now),
        )
        chapter_ids.append(chapter_id)
    db.commit()
    return fic_id, chapter_ids


def _record(client, *, fic_id=None, chapter_id=None, attachment_id=None, id=None,
            data=b'\x00' * 2048, filename='recording.webm', mime='audio/webm',
            transcribe=True, name='Commentary'):
    form = {'file': (io.BytesIO(data), filename, mime)}
    if fic_id is not None:
        form['ficId'] = fic_id
    if chapter_id is not None:
        form['chapterId'] = chapter_id
    if attachment_id is not None:
        form['attachmentId'] = attachment_id
    if id is not None:
        form['id'] = id
    if transcribe:
        form['transcribe'] = 'true'
    if name is not None:
        form['name'] = name
    return client.post(
        '/api/journal/recordings', data=form, content_type='multipart/form-data'
    )


def _refs(entry_id):
    return connection.get_db().execute(
        'SELECT fic_id, chapter_id FROM journal_entry_fic_refs'
        ' WHERE journal_entry_id=?',
        (entry_id,),
    ).fetchall()


# --- capture -----------------------------------------------------------------

def test_a_recording_becomes_an_entry_linked_to_the_chapter(client, monkeypatch):
    _run_pending_bg(monkeypatch)
    fic_id, (chapter_id,) = _fic()

    r = _record(client, fic_id=fic_id, chapter_id=chapter_id)
    assert r.status_code == 201
    body = r.get_json()
    assert body['ficId'] == fic_id

    # The audio is the entry, immediately, with no text in it — the transcript
    # is minutes away and stopping the recording is the save.
    entry = client.get(f"/api/journal/{body['id']}").get_json()
    assert entry['content'] == ''
    assert [a['kind'] for a in entry['attachments']] == ['audio']
    assert entry['attachments'][0]['transcriptStatus'] == 'running'
    assert [tuple(row) for row in _refs(body['id'])] == [(fic_id, chapter_id)]


def test_the_journal_feed_shows_which_chapter_it_was_about(client, monkeypatch):
    _run_pending_bg(monkeypatch)
    fic_id, (chapter_id,) = _fic('Worm Redux')

    entry_id = _record(client, fic_id=fic_id, chapter_id=chapter_id).get_json()['id']

    row = next(e for e in client.get('/api/journal?limit=100').get_json()
               if e['id'] == entry_id)
    assert [(r['ficId'], r['chapterId']) for r in row['ficRefs']] == [
        (fic_id, chapter_id)
    ]


def test_a_pdf_fic_links_to_the_fic_alone(client, monkeypatch):
    """A PDF has no chapters to name, so the reader sends none and the link is
    to the fic itself rather than to a chapter that does not exist."""
    _run_pending_bg(monkeypatch)
    fic_id, _ = _fic(chapters=0)

    entry_id = _record(client, fic_id=fic_id).get_json()['id']

    assert [tuple(row) for row in _refs(entry_id)] == [(fic_id, None)]


def test_the_transcript_lands_on_the_entry_afterwards(client, monkeypatch):
    """The whole point of the change: the recording is saved first and the text
    catches up, instead of the text being the only thing that was ever saved."""
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(journal_routes, '_do_attachment_audio',
                        lambda _p: 'That reveal recontextualises the whole arc.')
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg', lambda *a, **k: None)
    fic_id, (chapter_id,) = _fic()

    entry_id = _record(client, fic_id=fic_id, chapter_id=chapter_id).get_json()['id']
    assert len(jobs) == 1  # the transcription
    jobs[0]()

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['content'] == 'That reveal recontextualises the whole arc.'
    assert entry['rawContent'] == 'That reveal recontextualises the whole arc.'
    # And the link is still the one made at upload time.
    assert [tuple(row) for row in _refs(entry_id)] == [(fic_id, chapter_id)]


# --- the ways the two rows can drift apart -----------------------------------

def test_a_replayed_upload_links_once(client, monkeypatch):
    """The phone re-POSTs the same clip until it gets an ack, so a replay has to
    change nothing — not a second entry, not a second attachment, and not a
    second ref row."""
    _run_pending_bg(monkeypatch)
    fic_id, (chapter_id,) = _fic()
    entry_id, attachment_id = str(ULID()), str(ULID())

    first = _record(client, fic_id=fic_id, chapter_id=chapter_id,
                    id=entry_id, attachment_id=attachment_id)
    second = _record(client, fic_id=fic_id, chapter_id=chapter_id,
                     id=entry_id, attachment_id=attachment_id)
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.get_json()['id'] == entry_id

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert len(entry['attachments']) == 1
    assert [tuple(row) for row in _refs(entry_id)] == [(fic_id, chapter_id)]


def test_a_replay_makes_the_link_a_first_call_never_got_to(client, monkeypatch):
    """Convergence, not assumption: the first call can store the file and die
    before writing the link, and the replay is the only thing left that knows
    which chapter this was."""
    _run_pending_bg(monkeypatch)
    fic_id, (chapter_id,) = _fic()
    entry_id, attachment_id = str(ULID()), str(ULID())

    _record(client, id=entry_id, attachment_id=attachment_id)
    assert _refs(entry_id) == []

    _record(client, fic_id=fic_id, chapter_id=chapter_id,
            id=entry_id, attachment_id=attachment_id)
    assert [tuple(row) for row in _refs(entry_id)] == [(fic_id, chapter_id)]


def test_a_rejected_file_leaves_no_entry_and_no_link(client):
    fic_id, (chapter_id,) = _fic()
    entry_id = str(ULID())

    r = _record(client, fic_id=fic_id, chapter_id=chapter_id, id=entry_id,
                filename='notes.txt', mime='text/plain')

    assert r.status_code == 400
    assert client.get(f'/api/journal/{entry_id}').status_code == 404
    assert _refs(entry_id) == []


def test_a_malformed_fic_id_is_refused_before_anything_is_written(client):
    before = len(client.get('/api/journal?limit=100').get_json())
    assert _record(client, fic_id='not-a-ulid').status_code == 400
    assert len(client.get('/api/journal?limit=100').get_json()) == before


def test_an_unknown_fic_keeps_the_recording_and_drops_the_link(client, monkeypatch):
    """The audio is the irreplaceable half. A fic deleted while the phone was
    offline holding the clip must not cost the recording as well."""
    _run_pending_bg(monkeypatch)

    r = _record(client, fic_id=str(ULID()))

    assert r.status_code == 201
    entry_id = r.get_json()['id']
    assert [a['kind'] for a in
            client.get(f'/api/journal/{entry_id}').get_json()['attachments']] == ['audio']
    assert _refs(entry_id) == []


def test_a_chapter_from_another_fic_links_to_the_fic_alone(client, monkeypatch):
    """Half a link is better than a wrong one: the fic is still right, and the
    chapter that does not belong to it is dropped rather than stored."""
    _run_pending_bg(monkeypatch)
    fic_id, _ = _fic('This one')
    _, (other_chapter,) = _fic('Another one')

    entry_id = _record(client, fic_id=fic_id,
                       chapter_id=other_chapter).get_json()['id']

    assert [tuple(row) for row in _refs(entry_id)] == [(fic_id, None)]


def test_a_plain_journal_recording_links_to_nothing(client, monkeypatch):
    _run_pending_bg(monkeypatch)
    entry_id = _record(client, name='Recording').get_json()['id']
    assert _refs(entry_id) == []
    row = next(e for e in client.get('/api/journal?limit=100').get_json()
               if e['id'] == entry_id)
    assert row['ficRefs'] == []
