"""Recording an idea: one clip, two rows.

Record in the Ideas tab uploads through the journal's recording endpoint with
an `ideaId`, so the audio, the journal entry and the idea are created by a
single request that stays idempotent under replay. The transcript arrives later
and is written into both halves from one transcription.

What is pinned down here is the linkage and its failure modes — the ways the
two rows can drift apart: a replayed upload, an idea deleted while the
transcription was still running, and a user who typed into the idea before the
transcript landed.
"""
import io

import pytest
from ulid import ULID

from backend.db import connection
from backend.routes import ideas as ideas_routes
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))
    from backend.journal import storage

    monkeypatch.setattr(storage, '_root_override', None, raising=False)
    yield


def _run_pending_bg(monkeypatch):
    """Capture background jobs instead of queueing them, so a test can run them
    synchronously and assert on what they wrote. Both modules queue onto the
    same single worker, and both are patched: the transcription job is journal's
    and the polish/title job it fans out to is ideas'."""
    jobs = []
    monkeypatch.setattr(journal_routes, 'run_bg', jobs.append)
    monkeypatch.setattr(ideas_routes, 'run_bg', jobs.append)
    return jobs


def _record(client, *, idea_id=None, attachment_id=None, id=None,
            data=b'\x00' * 2048, filename='recording.webm', mime='audio/webm',
            transcribe=True, name=None, repo_id=None):
    form = {'file': (io.BytesIO(data), filename, mime)}
    if idea_id is not None:
        form['ideaId'] = idea_id
    if attachment_id is not None:
        form['attachmentId'] = attachment_id
    if id is not None:
        form['id'] = id
    if transcribe:
        form['transcribe'] = 'true'
    if name is not None:
        form['name'] = name
    if repo_id is not None:
        form['repoId'] = repo_id
    return client.post(
        '/api/journal/recordings', data=form, content_type='multipart/form-data'
    )


def _transcribes(monkeypatch, text='A grid of habits in the day view.'):
    """Stub the speech-to-text pass and the two journal model passes, leaving
    only the idea's own enrichment to be asserted on."""
    monkeypatch.setattr(journal_routes, '_do_attachment_audio', lambda _p: text)
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg', lambda *a, **k: None)


# --- capture -----------------------------------------------------------------

def test_recording_an_idea_creates_the_idea_and_the_entry_at_once(
    client, monkeypatch
):
    _run_pending_bg(monkeypatch)
    idea_id = str(ULID())
    r = _record(client, idea_id=idea_id)
    assert r.status_code == 201
    body = r.get_json()
    assert body['ideaId'] == idea_id

    # The idea exists immediately, empty, with the recording hanging off it —
    # the whole point is that stopping the recording is the save.
    idea = client.get(f'/api/ideas/{idea_id}').get_json()
    assert (idea['rawContent'], idea['content'], idea['title']) == ('', '', '')
    assert idea['recording']['entryId'] == body['id']
    assert idea['recording']['transcriptStatus'] == 'running'
    assert idea['recording']['url'] == (
        f"/api/journal/attachments/{body['attachment']['id']}/file"
    )

    entry = client.get(f"/api/journal/{body['id']}").get_json()
    assert entry['ideaId'] == idea_id
    assert [a['kind'] for a in entry['attachments']] == ['audio']


def test_the_journal_feed_links_back_to_the_idea(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    idea_id = str(ULID())
    entry_id = _record(client, idea_id=idea_id).get_json()['id']
    for job in list(jobs):
        job()

    feed = client.get('/api/journal?limit=100').get_json()
    row = next(e for e in feed if e['id'] == entry_id)
    assert row['ideaId'] == idea_id
    # Titled from the first line while the generated title is still empty, the
    # same way the Ideas list names it.
    assert row['ideaTitle'] == 'A grid of habits in the day view.'


def test_a_typed_idea_has_no_recording_and_a_plain_entry_no_link(client):
    idea_id = client.post('/api/ideas', json={'rawContent': 'typed'}).get_json()['id']
    assert client.get(f'/api/ideas/{idea_id}').get_json()['recording'] is None

    entry_id = client.post(
        '/api/journal', json={'content': 'A day.'}
    ).get_json()['id']
    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['ideaId'] is None and entry['ideaTitle'] is None


def _repo(slug, *, default=False):
    """A registered repository, straight into the table — the real registration
    path clones over the network."""
    db = connection.get_db()
    db.execute(
        'INSERT INTO repos(id, slug, name, remote_url, is_default, created_at,'
        ' updated_at) VALUES (?,?,?,?,?,0,0)',
        (slug, slug, slug, f'https://example.invalid/{slug}.git', 1 if default else 0),
    )
    db.commit()
    return slug


def test_an_idea_recording_takes_the_default_repo(client, monkeypatch):
    _run_pending_bg(monkeypatch)
    _repo('repo-1', default=True)
    idea_id = str(ULID())
    _record(client, idea_id=idea_id)
    assert client.get(f'/api/ideas/{idea_id}').get_json()['repoId'] == 'repo-1'


def test_an_explicit_repo_wins(client, monkeypatch):
    """The Ideas list can be filtered to one repository, and a capture made
    under that filter belongs to it rather than to the default."""
    _run_pending_bg(monkeypatch)
    _repo('repo-1', default=True)
    _repo('repo-2')
    idea_id = str(ULID())
    _record(client, idea_id=idea_id, repo_id='repo-2')
    assert client.get(f'/api/ideas/{idea_id}').get_json()['repoId'] == 'repo-2'


def test_a_malformed_idea_id_is_refused_before_anything_is_written(client):
    before = len(client.get('/api/ideas').get_json())
    r = _record(client, idea_id='not-a-ulid')
    assert r.status_code == 400
    assert len(client.get('/api/ideas').get_json()) == before


def test_a_rejected_file_leaves_no_idea_behind(client):
    """The entry is rolled back when the upload is refused, and the idea must
    go the same way — an empty idea with no recording to explain it is worse
    than nothing in a backlog."""
    before = len(client.get('/api/ideas').get_json())
    idea_id = str(ULID())
    r = _record(client, idea_id=idea_id, filename='notes.txt', mime='text/plain')
    assert r.status_code == 400
    assert client.get(f'/api/ideas/{idea_id}').status_code == 404
    assert len(client.get('/api/ideas').get_json()) == before


# --- the transcript reaching both halves -------------------------------------

def test_one_transcription_fills_the_entry_and_the_idea(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    calls = []
    monkeypatch.setattr(journal_routes, '_do_attachment_audio',
                        lambda _p: calls.append(1) or 'A grid of habits.')

    idea_id = str(ULID())
    entry_id = _record(client, idea_id=idea_id).get_json()['id']

    assert len(jobs) == 1  # the transcription
    jobs[0]()
    assert len(calls) == 1  # transcribed once, not once per row

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['content'] == 'A grid of habits.'
    idea = client.get(f'/api/ideas/{idea_id}').get_json()
    assert idea['rawContent'] == 'A grid of habits.'
    assert idea['recording']['transcriptStatus'] == 'done'


def test_the_transcript_is_polished_and_titled_like_any_dictated_idea(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    monkeypatch.setattr('backend.ai.idea_polish.polish_idea',
                        lambda text, memory=None: 'A grid of habits, cleaned up.')
    monkeypatch.setattr('backend.ai.idea_title.generate_idea_title',
                        lambda text, memory=None: 'Habit grid')
    monkeypatch.setattr('backend.memory.get_memory', lambda: '')

    idea_id = str(ULID())
    _record(client, idea_id=idea_id)
    while jobs:
        jobs.pop(0)()

    idea = client.get(f'/api/ideas/{idea_id}').get_json()
    assert idea['rawContent'] == 'A grid of habits in the day view.'
    assert idea['content'] == 'A grid of habits, cleaned up.'
    assert idea['title'] == 'Habit grid'


def test_a_transcript_never_overwrites_what_the_user_typed_meanwhile(
    client, monkeypatch
):
    """The idea is open in the detail pane while the clip is transcribing, and
    typing into it saves immediately. The transcript arriving afterwards must
    not take that away."""
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)

    idea_id = str(ULID())
    _record(client, idea_id=idea_id)
    client.patch(f'/api/ideas/{idea_id}', json={'content': 'Typed while waiting'})
    # rawContent is what the transcript targets; set it the way the detail pane
    # does when the body is edited before the transcript lands.
    client.patch(f'/api/ideas/{idea_id}', json={'rawContent': 'Typed while waiting'})
    jobs[0]()

    idea = client.get(f'/api/ideas/{idea_id}').get_json()
    assert idea['rawContent'] == 'Typed while waiting'


def test_a_failed_transcription_leaves_the_idea_empty_and_says_so(
    client, monkeypatch
):
    """No text is better than a wrong one, and the audio is still there — the
    detail pane offers the recording and the error rather than a blank row."""
    jobs = _run_pending_bg(monkeypatch)

    def _boom(_path):
        raise RuntimeError('No speech found in the recording')

    monkeypatch.setattr(journal_routes, '_do_attachment_audio', _boom)
    idea_id = str(ULID())
    _record(client, idea_id=idea_id)
    jobs[0]()

    idea = client.get(f'/api/ideas/{idea_id}').get_json()
    assert idea['rawContent'] == ''
    assert idea['recording']['transcriptStatus'] == 'error'
    assert idea['recording']['transcriptError'] == 'No speech found in the recording'


# --- deletion ----------------------------------------------------------------

def test_deleting_the_idea_keeps_the_entry_and_drops_the_link(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    idea_id = str(ULID())
    entry_id = _record(client, idea_id=idea_id).get_json()['id']
    for job in list(jobs):
        job()

    client.delete(f'/api/ideas/{idea_id}')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['content'] == 'A grid of habits in the day view.'
    assert [a['kind'] for a in entry['attachments']] == ['audio']
    assert entry['ideaId'] is None and entry['ideaTitle'] is None


def test_a_transcript_landing_after_the_idea_is_deleted_is_dropped(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    idea_id = str(ULID())
    entry_id = _record(client, idea_id=idea_id).get_json()['id']

    client.delete(f'/api/ideas/{idea_id}')
    jobs[0]()  # the transcription finishing minutes later

    assert client.get(f'/api/ideas/{idea_id}').status_code == 404
    # The journal half is unaffected: the recording was still worth keeping.
    assert client.get(f'/api/journal/{entry_id}').get_json()['content'] == (
        'A grid of habits in the day view.'
    )


def test_deleting_the_entry_leaves_the_idea_with_its_text(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    idea_id = str(ULID())
    entry_id = _record(client, idea_id=idea_id).get_json()['id']
    for job in list(jobs):
        job()

    client.delete(f'/api/journal/{entry_id}')

    idea = client.get(f'/api/ideas/{idea_id}').get_json()
    assert idea['rawContent'] == 'A grid of habits in the day view.'
    # The audio went with the entry, so the pane stops offering a player.
    assert idea['recording'] is None


# --- replay ------------------------------------------------------------------

def test_a_replayed_idea_recording_creates_nothing_new(client):
    idea_id, entry_id, attachment_id = str(ULID()), str(ULID()), str(ULID())
    before = len(client.get('/api/ideas').get_json())

    first = _record(client, idea_id=idea_id, id=entry_id,
                    attachment_id=attachment_id)
    second = _record(client, idea_id=idea_id, id=entry_id,
                     attachment_id=attachment_id)
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.get_json()['id'] == entry_id

    assert len(client.get('/api/ideas').get_json()) == before + 1
    assert len(client.get(f'/api/journal/{entry_id}').get_json()['attachments']) == 1


def test_a_replay_links_an_idea_the_first_call_never_got_to_write(client):
    """The upload landed and the process died before the idea row existed. The
    retry has to converge, not skip past it."""
    idea_id, entry_id, attachment_id = str(ULID()), str(ULID()), str(ULID())
    _record(client, id=entry_id, attachment_id=attachment_id)

    db = connection.get_db()
    db.execute('DELETE FROM ideas WHERE id=?', (idea_id,))
    db.execute('UPDATE journal_entries SET idea_id=NULL WHERE id=?', (entry_id,))
    db.commit()

    _record(client, idea_id=idea_id, id=entry_id, attachment_id=attachment_id)
    assert client.get(f'/api/ideas/{idea_id}').status_code == 200
    assert client.get(f'/api/journal/{entry_id}').get_json()['ideaId'] == idea_id
