"""Audio/photo attachments on journal entries.

The behaviours worth pinning down: an upload never triggers the *transcript*
(opt-in, because on this hardware it costs a real CPU transcription), a failed
transcription lands on the row as an error the UI can show rather than
disappearing into a background thread, an upload *does* auto-fire the
non-speech audio description when an audio model is configured (but stays
idle, not erroring, when it isn't), and an image upload picks up its capture
location from EXIF GPS the same way the food log does.
"""
import io

import pytest
from PIL import Image
from ulid import ULID

from backend.ai import audio_description as audio_ai
from backend.ai import images as images_ai
from backend.journal import storage
from backend.routes import journal as journal_routes


def _exif_jpeg(gps=('N', (43.0, 39.0, 11.0), 'W', (79.0, 22.0, 59.0))):
    """A tiny JPEG carrying a GPS fix (or none, if gps=None)."""
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    exif = img.getexif()
    if gps:
        lat_ref, lat, lon_ref, lon = gps
        g = exif.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = lat_ref, lat, lon_ref, lon
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


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


def test_upload_accepts_a_non_media_file(client, entry_id):
    """This used to be `test_upload_rejects_unsupported_types`. The composer now
    offers "attach a file" without qualification, so a PDF is a first-class
    attachment stored under `kind='file'` — the media tables still decide audio
    vs video vs image, and still gate everything that reaches a model."""
    r = _upload(client, entry_id, filename='notes.pdf', mime='application/pdf')
    assert r.status_code == 201
    a = r.get_json()
    assert a['kind'] == 'file'
    assert a['name'] == 'notes.pdf'


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


# --- location (EXIF GPS) and auto-fired audio description --------------------

def test_upload_extracts_gps_from_image_exif(client, entry_id):
    a = _upload(client, entry_id, filename='view.jpg', mime='image/jpeg',
                data=_exif_jpeg()).get_json()
    assert a['latitude'] == pytest.approx(43.653056)
    assert a['longitude'] == pytest.approx(-79.383056)


def test_upload_leaves_location_null_without_gps(client, entry_id):
    a = _upload(client, entry_id, filename='view.jpg', mime='image/jpeg',
                data=_exif_jpeg(gps=None)).get_json()
    assert a['latitude'] is None
    assert a['longitude'] is None


def test_audio_upload_has_no_location(client, entry_id):
    a = _upload(client, entry_id).get_json()
    assert a['latitude'] is None
    assert a['longitude'] is None


def test_upload_does_not_auto_describe_when_unconfigured(client, entry_id):
    """No llama_audio_model is set on a fresh DB, the shipped default — an
    upload must not land on a dead 'no model configured' error."""
    a = _upload(client, entry_id).get_json()
    assert a['descriptionStatus'] == 'idle'
    assert a['description'] is None


def test_upload_auto_fires_audio_description_when_configured(client, entry_id, monkeypatch):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-12b-omni'}
    )
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio_description',
        lambda _p, _n: 'A dog barks twice in the background.',
    )

    a = _upload(client, entry_id).get_json()
    assert a['descriptionStatus'] == 'running'
    assert len(jobs) == 1

    jobs[0]()
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['description'] == 'A dog barks twice in the background.'
    assert got['descriptionStatus'] == 'done'


def test_image_upload_never_auto_describes(client, entry_id, monkeypatch):
    """The *audio* description path stays audio/video only. Captioning is the
    separate transcript column, tested below."""
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-12b-omni'}
    )
    monkeypatch.setattr(images_ai, 'get_provider_config', lambda: {})
    jobs = _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id, filename='sink.jpg', mime='image/jpeg').get_json()
    assert a['descriptionStatus'] == 'idle'
    assert jobs == []


def test_image_upload_auto_captions_when_vision_is_configured(
    client, entry_id, monkeypatch
):
    """Captioning used to wait for an explicit press of Transcribe, which meant
    the entry's title — generated from the caption — never saw the photo."""
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_caption',
        lambda _p, _n: 'A cat asleep on a radiator.',
    )

    a = _upload(client, entry_id, filename='cat.jpg', mime='image/jpeg').get_json()
    assert a['transcriptStatus'] == 'running'
    assert len(jobs) == 1

    jobs[0]()
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcript'] == 'A cat asleep on a radiator.'
    assert got['transcriptStatus'] == 'done'


def test_image_upload_stays_idle_when_vision_is_off(client, entry_id, monkeypatch):
    """Not 'error'. An unconfigured feature must not stamp every photo with a
    failure — that is what made the dead `gemma4-vision` alias look like normal
    behaviour for months."""
    monkeypatch.setattr(images_ai, 'get_provider_config', lambda: {})
    jobs = _run_pending_bg(monkeypatch)

    a = _upload(client, entry_id, filename='cat.jpg', mime='image/jpeg').get_json()
    assert a['transcriptStatus'] == 'idle'
    assert jobs == []


def test_audio_upload_still_does_not_auto_transcribe(client, entry_id, monkeypatch):
    """The asymmetry is deliberate: a caption is one vision call, a transcript is
    minutes of Whisper."""
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    monkeypatch.setattr(audio_ai, 'get_provider_config', lambda: {})
    jobs = _run_pending_bg(monkeypatch)

    a = _upload(client, entry_id).get_json()
    assert a['transcriptStatus'] == 'idle'
    assert jobs == []


def test_a_file_attachment_is_never_captioned(client, entry_id, monkeypatch):
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    jobs = _run_pending_bg(monkeypatch)

    a = _upload(client, entry_id, filename='taxes.pdf', mime='application/pdf').get_json()
    assert a['kind'] == 'file'
    assert a['transcriptStatus'] == 'idle'
    assert jobs == []


def test_a_file_attachment_cannot_be_transcribed_by_hand_either(client, entry_id):
    a = _upload(client, entry_id, filename='taxes.pdf', mime='application/pdf').get_json()
    r = client.post(f"/api/journal/attachments/{a['id']}/transcribe")
    assert r.status_code == 400
    # And the row is not left parked in 'running'.
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['transcriptStatus'] == 'idle'


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
    # Not media: stored as the catch-all kind rather than refused.
    ('application/octet-stream', 'notes.pdf', ('pdf', 'file')),
    ('text/plain', 'shopping list', ('bin', 'file')),
    (None, None, ('bin', 'file')),
    # An extension that could become a path segment, or is simply absurd, does
    # not survive into `attachment.<ext>`.
    (None, 'sneaky./..\\x', ('bin', 'file')),
    (None, 'archive.tar.gz', ('gz', 'file')),
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


@pytest.mark.parametrize('ext', ['../etc', 'a/b', 'has.dot', '', 'w' * 13])
def test_attachment_path_refuses_a_malformed_extension(ext):
    """Not "an extension we don't recognise" any more — `kind='file'` stores
    whatever `safe_ext` let through. What is still refused is anything that
    could introduce a path segment or separator."""
    assert storage.attachment_path('01ABC', ext) is None


def test_attachment_path_accepts_an_unrecognised_but_safe_extension(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path))
    assert storage.attachment_path('01ABC', 'pdf').name == 'attachment.pdf'


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


# --- describe-audio ------------------------------------------------------------

def test_describe_audio_queues_and_reports_running(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id).get_json()

    r = client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    assert r.status_code == 202
    assert r.get_json()['descriptionStatus'] == 'running'
    assert len(jobs) == 1


def test_describe_audio_refuses_to_queue_twice(client, entry_id, monkeypatch):
    _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    r = client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    assert r.status_code == 409


def test_describe_audio_refuses_an_image(client, entry_id, monkeypatch):
    _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id, filename='sink.jpg', mime='image/jpeg').get_json()
    r = client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    assert r.status_code == 400


def test_a_completed_audio_description_lands_on_the_row(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    seen = {}

    def _describe(path, name):
        seen['name'] = name
        return 'A dog barks twice in the background.'
    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description', _describe)

    a = _upload(client, entry_id, name='backyard recording').get_json()
    client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    jobs[0]()

    assert seen['name'] == 'backyard recording'
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['description'] == 'A dog barks twice in the background.'
    assert got['descriptionStatus'] == 'done'
    assert got['descriptionError'] is None


def test_a_failed_audio_description_is_recorded_not_swallowed(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)

    def _boom(_path, _name):
        raise RuntimeError('No audio-description model configured')
    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description', _boom)

    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    jobs[0]()

    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['descriptionStatus'] == 'error'
    assert got['descriptionError'] == 'No audio-description model configured'
    assert got['description'] is None


def test_video_can_also_be_described(client, entry_id, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio_description',
        lambda _p, _n: 'Wind noise and distant traffic.',
    )
    a = _upload(client, entry_id, filename='clip.mov', mime='video/quicktime').get_json()
    r = client.post(f"/api/journal/attachments/{a['id']}/describe-audio")
    assert r.status_code == 202
    jobs[0]()

    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['description'] == 'Wind noise and distant traffic.'


def test_running_descriptions_are_reset_at_startup(client, entry_id, monkeypatch):
    from backend.db import connection

    _run_pending_bg(monkeypatch)
    a = _upload(client, entry_id).get_json()
    client.post(f"/api/journal/attachments/{a['id']}/describe-audio")

    connection._reset_stale_attachment_transcripts(connection.get_db())
    got = client.get(f'/api/journal/{entry_id}/attachments').get_json()[0]
    assert got['descriptionStatus'] == 'idle'


# --- recording entries (the bottom bar's Record button) ----------------------
#
# The clip is the entry: nothing goes to speech-to-text, so the row is created
# with an empty body and the audio hangs off it. The point of the one-shot route
# is that a rejected file leaves no empty entry behind.

def _record(client, *, filename='recording.webm', data=b'\x00' * 2048,
            mime='audio/webm', name=None, id=None, attachment_id=None,
            transcribe=False):
    form = {'file': (io.BytesIO(data), filename, mime)}
    if name is not None:
        form['name'] = name
    if id is not None:
        form['id'] = id
    if attachment_id is not None:
        form['attachmentId'] = attachment_id
    if transcribe:
        form['transcribe'] = 'true'
    return client.post(
        '/api/journal/recordings', data=form, content_type='multipart/form-data'
    )


def test_recording_creates_an_entry_carrying_the_audio(client):
    r = _record(client)
    assert r.status_code == 201
    body = r.get_json()

    entry = client.get(f"/api/journal/{body['id']}").get_json()
    assert entry['content'] == ''
    # Not transcribed, and not queued for transcription either.
    assert entry['rawContent'] is None
    assert [(a['kind'], a['name'], a['transcriptStatus']) for a in entry['attachments']] == [
        ('audio', 'Recording', 'idle')
    ]


def test_recording_honours_a_supplied_name(client):
    body = _record(client, name='Walking home').get_json()
    entry = client.get(f"/api/journal/{body['id']}").get_json()
    assert entry['attachments'][0]['name'] == 'Walking home'


def test_recording_generates_no_metadata(client, monkeypatch):
    """An empty body would only produce an invented title and tags."""
    called = []
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg',
                        lambda *a, **k: called.append(a))
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: called.append(a))
    _record(client)
    assert called == []


def test_journal_recording_keeps_audio_and_transcribes_into_entry(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    monkeypatch.setattr(journal_routes, '_do_attachment_audio',
                        lambda _path: 'A thought worth keeping.')
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a: None)
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg', lambda *a: None)

    body = _record(client, transcribe=True).get_json()
    entry = client.get(f"/api/journal/{body['id']}").get_json()
    assert entry['content'] == ''
    assert entry['attachments'][0]['transcriptStatus'] == 'running'

    jobs[0]()
    entry = client.get(f"/api/journal/{body['id']}").get_json()
    assert entry['content'] == 'A thought worth keeping.'
    assert entry['rawContent'] == 'A thought worth keeping.'
    assert entry['attachments'][0]['transcript'] == 'A thought worth keeping.'


def test_recording_needs_a_file(client):
    before = len(client.get('/api/journal?limit=100').get_json())
    r = client.post('/api/journal/recordings', data={},
                    content_type='multipart/form-data')
    assert r.status_code == 400
    assert len(client.get('/api/journal?limit=100').get_json()) == before


def test_a_rejected_file_leaves_no_empty_entry_behind(client):
    before = len(client.get('/api/journal?limit=100').get_json())
    # media_only: the recording route still refuses a non-media blob. A
    # text/plain arriving there is the phone sending the wrong thing, not
    # somebody choosing to attach a document.
    r = _record(client, filename='notes.txt', mime='text/plain')
    assert r.status_code == 400
    assert len(client.get('/api/journal?limit=100').get_json()) == before


def test_an_empty_recording_leaves_no_entry_behind(client):
    before = len(client.get('/api/journal?limit=100').get_json())
    r = _record(client, data=b'')
    assert r.status_code == 400
    assert len(client.get('/api/journal?limit=100').get_json()) == before


# --- replaying a recording upload -------------------------------------------
#
# The phone keeps a recording in IndexedDB until the server confirms it landed,
# and re-POSTs it on every reconnect until then. A response that never made it
# back therefore produces a second identical request as a matter of course, so
# "the same upload twice" is the normal path and has to be a no-op — otherwise
# the fix for losing recordings would trade it for duplicating them.

def _entry_count(client):
    return len(client.get('/api/journal?limit=100').get_json())


def test_a_replayed_recording_creates_nothing_new(client):
    entry_id, attachment_id = str(ULID()), str(ULID())
    before = _entry_count(client)

    first = _record(client, id=entry_id, attachment_id=attachment_id)
    second = _record(client, id=entry_id, attachment_id=attachment_id)

    # The replay answers like a success so the phone can drop its local copy.
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.get_json()['id'] == second.get_json()['id'] == entry_id
    assert first.get_json()['attachment']['id'] == attachment_id
    assert second.get_json()['attachment']['id'] == attachment_id

    assert _entry_count(client) == before + 1
    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert len(entry['attachments']) == 1
    # One directory on disk, holding one file.
    assert len(list(storage.attachment_dir(attachment_id).iterdir())) == 1


def test_a_replay_does_not_rewrite_the_stored_file(client):
    """The check for an id we already hold comes before the file is read: a
    replay must not stream a hundred megabytes to disk to discover it was
    already there."""
    entry_id, attachment_id = str(ULID()), str(ULID())
    _record(client, id=entry_id, attachment_id=attachment_id, data=b'\x01' * 2048)

    path = storage.attachment_dir(attachment_id) / 'attachment.webm'
    before = path.stat()

    # Different bytes, same ids. The stored file must be the original.
    _record(client, id=entry_id, attachment_id=attachment_id, data=b'\x02' * 4096)

    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert path.read_bytes() == b'\x01' * 2048


def test_a_replay_finds_its_entry_without_being_told_it(client):
    """Only the attachment id is needed to recognise a recording we hold."""
    attachment_id = str(ULID())
    first = _record(client, attachment_id=attachment_id)
    entry_id = first.get_json()['id']

    second = _record(client, attachment_id=attachment_id)
    assert second.status_code == 201
    assert second.get_json()['id'] == entry_id


def test_a_rejected_upload_keeps_an_entry_it_did_not_create(client):
    """The rollback deletes only an entry *this* request created. A retry that
    reuses the entry id but carries a bad file must not take the good entry with
    it."""
    entry_id = str(ULID())
    _record(client, id=entry_id, attachment_id=str(ULID()))

    r = _record(client, id=entry_id, attachment_id=str(ULID()),
                filename='notes.txt', mime='text/plain')
    assert r.status_code == 400

    entry = client.get(f'/api/journal/{entry_id}')
    assert entry.status_code == 200
    assert len(entry.get_json()['attachments']) == 1


def test_a_malformed_client_id_is_refused(client):
    """An attachment id becomes a directory name. Substituting a fresh one
    instead would also break the replay the client is counting on, so this is a
    400 rather than a silent fallback."""
    before = _entry_count(client)
    for field in ('id', 'attachment_id'):
        r = _record(client, **{field: '../../etc/passwd'})
        assert r.status_code == 400, field
    assert _entry_count(client) == before
