"""Audio/photo attachments on journal entries.

The two behaviours worth pinning down: an upload never triggers AI work (the
transcript is opt-in, because on this hardware it costs a real CPU
transcription), and a failed transcription lands on the row as an error the UI
can show rather than disappearing into a background thread.
"""
import io

import pytest

from backend.ai import images as images_ai
from backend.journal import storage
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


@pytest.fixture
def entry_id(client):
    return client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']


def _upload(client, entry_id, *, filename='memo.m4a', data=b'\x00' * 2048,
            mime='audio/mp4', name=None):
    form = {'file': (io.BytesIO(data), filename, mime)}
    if name is not None:
        form['name'] = name
    return client.post(
        f'/api/journal/{entry_id}/attachments',
        data=form, content_type='multipart/form-data',
    )


def _run_pending_bg(monkeypatch):
    """Capture the background job instead of queueing it, so the test can run it
    synchronously and assert on what it wrote."""
    jobs = []
    monkeypatch.setattr(journal_routes, 'run_bg', jobs.append)
    return jobs


# --- upload ------------------------------------------------------------------

def test_upload_stores_file_and_defaults_name_to_filename(client, entry_id):
    r = _upload(client, entry_id, filename='voice-memo-004.m4a')
    assert r.status_code == 201
    a = r.get_json()
    assert a['kind'] == 'audio'
    assert a['name'] == 'voice-memo-004.m4a'
    assert a['size'] == 2048
    assert a['url'] == f"/api/journal/attachments/{a['id']}/file"
    # Never transcribed on upload — that is the user's explicit choice.
    assert a['transcript'] is None
    assert a['transcriptStatus'] == 'idle'
    # `path` is a server-side location and must not leak to the client.
    assert 'path' not in a


def test_upload_honours_a_supplied_name(client, entry_id):
    a = _upload(client, entry_id, name='Walk home, thinking about the parser').get_json()
    assert a['name'] == 'Walk home, thinking about the parser'


def test_upload_detects_video(client, entry_id):
    a = _upload(client, entry_id, filename='IMG_0043.MOV', mime='video/quicktime').get_json()
    assert a['kind'] == 'video'


def test_upload_detects_images(client, entry_id):
    a = _upload(client, entry_id, filename='fence.png', data=b'\x89PNG' + b'\x00' * 500,
                mime='image/png').get_json()
    assert a['kind'] == 'image'


def test_upload_accepts_a_nameless_file(client, entry_id):
    """A voice memo dragged out of the iOS Voice Memos app is a File with an
    empty name, so FormData sends filename="". Rejecting that turned a working
    drag-and-drop into "file is required"; the mime type is enough."""
    r = _upload(client, entry_id, filename='', mime='audio/mp4')
    assert r.status_code == 201
    a = r.get_json()
    assert a['kind'] == 'audio'
    # Falls back to a readable label rather than an empty row.
    assert a['name'] == 'Audio'


def test_upload_names_a_nameless_video(client, entry_id):
    a = _upload(client, entry_id, filename='', mime='video/quicktime').get_json()
    assert (a['kind'], a['name']) == ('video', 'Video')


def test_upload_still_rejects_a_missing_file_part(client, entry_id):
    r = client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'name': 'no file here'}, content_type='multipart/form-data',
    )
    assert r.status_code == 400
    assert r.get_json()['error'] == 'file is required'


def test_upload_rejects_unsupported_types(client, entry_id):
    r = _upload(client, entry_id, filename='notes.pdf', mime='application/pdf')
    assert r.status_code == 400


def test_upload_rejects_an_empty_file(client, entry_id):
    r = _upload(client, entry_id, data=b'')
    assert r.status_code == 400


def test_upload_rejects_an_oversized_file(client, entry_id, monkeypatch):
    monkeypatch.setitem(journal_routes._MAX_BYTES_BY_KIND, 'audio', 100)
    r = _upload(client, entry_id, data=b'\x00' * 500)
    assert r.status_code == 413


def test_a_rejected_upload_leaves_nothing_behind(client, entry_id, monkeypatch):
    """The file streams to disk before its size is known, so an over-limit
    upload has to clean up after itself rather than orphaning a directory."""
    monkeypatch.setitem(journal_routes._MAX_BYTES_BY_KIND, 'audio', 100)
    _upload(client, entry_id, data=b'\x00' * 500)

    assert client.get(f'/api/journal/{entry_id}/attachments').get_json() == []
    root = storage.journal_root()
    assert not root.exists() or list(root.iterdir()) == []


def test_upload_404s_for_an_unknown_entry(client):
    assert _upload(client, 'nope').status_code == 404


def test_uploads_keep_their_order(client, entry_id):
    _upload(client, entry_id, name='first')
    _upload(client, entry_id, name='second')
    names = [a['name'] for a in client.get(f'/api/journal/{entry_id}/attachments').get_json()]
    assert names == ['first', 'second']


# --- listing -----------------------------------------------------------------

def test_entries_carry_their_attachments(client, entry_id):
    _upload(client, entry_id, name='memo')
    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert [a['name'] for a in entry['attachments']] == ['memo']

    listed = client.get('/api/journal?limit=50&offset=0').get_json()
    assert [a['name'] for a in listed[0]['attachments']] == ['memo']


def test_an_entry_without_attachments_gets_an_empty_list(client, entry_id):
    assert client.get(f'/api/journal/{entry_id}').get_json()['attachments'] == []


# --- serving, renaming, deleting ---------------------------------------------

def test_file_is_served_back(client, entry_id):
    a = _upload(client, entry_id, data=b'\x01' * 2048).get_json()
    r = client.get(a['url'])
    assert r.status_code == 200
    assert r.data == b'\x01' * 2048


def test_rename(client, entry_id):
    a = _upload(client, entry_id).get_json()
    r = client.patch(f"/api/journal/attachments/{a['id']}", json={'name': 'The leak under the sink'})
    assert r.status_code == 200
    assert r.get_json()['name'] == 'The leak under the sink'


def test_rename_rejects_an_empty_name(client, entry_id):
    a = _upload(client, entry_id).get_json()
    r = client.patch(f"/api/journal/attachments/{a['id']}", json={'name': '  '})
    assert r.status_code == 400


def test_delete_removes_the_row_and_the_file(client, entry_id):
    a = _upload(client, entry_id).get_json()
    directory = storage.attachment_dir(a['id'])
    assert directory.is_dir()

    assert client.delete(f"/api/journal/attachments/{a['id']}").status_code == 200
    assert client.get(f'/api/journal/{entry_id}/attachments').get_json() == []
    assert not directory.exists()
    assert client.get(a['url']).status_code == 404


def test_deleting_the_entry_cascades_to_its_attachments(client, entry_id):
    a = _upload(client, entry_id).get_json()
    client.delete(f'/api/journal/{entry_id}')
    assert client.get(f"/api/journal/attachments/{a['id']}/file").status_code == 404


# --- transcription / captioning ----------------------------------------------

def test_transcribe_queues_and_reports_running(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id).get_json()

    r = client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    assert r.status_code == 202
    assert r.get_json()['transcriptStatus'] == 'running'
    assert len(jobs) == 1


def test_transcribe_refuses_to_queue_twice(client, entry_id, monkeypatch):
    _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    assert client.post(f"/api/journal/attachments/{a['id']}/transcribe").status_code == 409


def test_a_completed_transcription_lands_on_the_row(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio', lambda _p: 'So today was rough.'
    )
    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    jobs[0]()

    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcript'] == 'So today was rough.'
    assert got['transcriptStatus'] == 'done'
    assert got['transcriptError'] is None


def test_a_failed_transcription_is_recorded_not_swallowed(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)

    def _boom(_path):
        raise RuntimeError('Whisper model failed to load')
    monkeypatch.setattr(journal_routes, '_do_attachment_audio', _boom)

    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    jobs[0]()

    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcriptStatus'] == 'error'
    assert got['transcriptError'] == 'Whisper model failed to load'
    assert got['transcript'] is None


def test_video_is_transcribed_not_captioned(client, entry_id, monkeypatch):
    """ffmpeg pulls the audio track out of the container, so a clip filmed to
    talk into yields its speech — the vision path is never involved."""
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio', lambda _p: 'Talking at the camera.'
    )
    def _never(*a, **k):
        raise AssertionError('video must not go through the captioner')
    monkeypatch.setattr(journal_routes, '_do_attachment_caption', _never)

    a = _upload(client, entry_id, filename='clip.mov', mime='video/quicktime').get_json()
    assert a['kind'] == 'video'
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    jobs[0]()

    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcript'] == 'Talking at the camera.'
    assert got['transcriptStatus'] == 'done'


def test_an_image_is_captioned_with_its_name_as_context(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    seen = {}

    def _caption(path, hint=None):
        seen['hint'] = hint
        return 'A cast-iron pipe with a damp patch beneath the joint.'
    monkeypatch.setattr(images_ai, 'caption_image', _caption)

    a = _upload(client, entry_id, filename='sink.jpg', mime='image/jpeg',
                name='the leak under the sink').get_json()
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    jobs[0]()

    assert seen['hint'] == 'the leak under the sink'
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcript'].startswith('A cast-iron pipe')


def test_captioning_reports_that_vision_is_off(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id, filename='sink.jpg', mime='image/jpeg').get_json()
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    jobs[0]()

    # No llama_vision_model is set on a fresh DB, which is the shipped default.
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcriptStatus'] == 'error'
    assert 'No vision model configured' in got['transcriptError']


# --- backend/journal/storage.py (pure) ---------------------------------------

@pytest.mark.parametrize('mime, filename, expected', [
    ('audio/mp4', 'memo.m4a', ('m4a', 'audio')),
    # A mime type with a parameter still has to resolve.
    ('audio/webm;codecs=opus', 'clip.webm', ('webm', 'audio')),
    # Phones hand over extensionless names; the mime carries it instead.
    ('image/jpeg', 'IMG_0042', ('jpg', 'image')),
    # An unhelpful mime falls through to the suffix.
    ('application/octet-stream', 'memo.mp3', ('mp3', 'audio')),
    ('application/octet-stream', 'notes.pdf', None),
    (None, None, None),
    # iOS camera roll.
    ('video/quicktime', 'IMG_0043.MOV', ('mov', 'video')),
    ('video/mp4', 'clip.mp4', ('mp4', 'video')),
    # The same container carries either; only the mime separates them. An
    # audio-only MP4 is also normalized to its conventional .m4a extension.
    ('audio/mp4', 'thing.mp4', ('m4a', 'audio')),
    ('video/webm', 'thing.webm', ('webm', 'video')),
    # With no usable mime, an ambiguous extension defaults to video — showing
    # audio in a <video> costs a blank frame, the reverse loses the picture.
    (None, 'thing.mp4', ('mp4', 'video')),
    (None, 'memo.m4a', ('m4a', 'audio')),
])
def test_resolve_upload(mime, filename, expected):
    assert storage.resolve_upload(mime, filename) == expected


def test_attachment_path_refuses_a_traversing_id():
    assert storage.attachment_path('../../etc', 'mp3') is None


def test_attachment_path_refuses_an_unaccepted_extension():
    assert storage.attachment_path('01ABC', 'exe') is None


def test_running_transcripts_are_reset_at_startup(client, entry_id, monkeypatch):
    """A background job's thread dies with the process; the 'running' row does
    not. Same reasoning as the stale fic-download reset."""
    from backend.db import connection

    _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/transcribe")

    connection._reset_stale_attachment_transcripts(connection.get_db())
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcriptStatus'] == 'idle'
