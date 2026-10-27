"""Live Photos, uploaded videos, and the date a photo or video was taken.

What's pinned down: the phone's `capturedAt` is kept on the attachment (and EXIF
is the fallback when it is absent); a Live Photo is one image row whose clip is
served from its own route and never leaks its path; a motion part is refused
beside anything but an image; a replay with a motion part stores nothing twice;
an iPhone HEIC still is re-encoded to JPEG with its EXIF intact; and an
uploaded video gets a poster and its length when ffmpeg is there to read them.
"""
import io
import shutil
import subprocess
from datetime import datetime, timezone

import pytest
from PIL import Image
from ulid import ULID

from backend.journal import storage
from backend.journal import video as journal_video
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


def _jpeg(taken='2026:03:14 09:41:02'):
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    exif = img.getexif()
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _post(client, entry_id, *, data, filename, mime, motion=None, **form):
    body = {'file': (io.BytesIO(data), filename, mime), **form}
    if motion is not None:
        body['motion'] = (io.BytesIO(motion), 'motion.mov', 'video/quicktime')
    return client.post(f'/api/journal/{entry_id}/attachments', data=body,
                       content_type='multipart/form-data')


# --- capture date -------------------------------------------------------------

def test_the_phones_capture_time_is_kept(client, entry_id):
    a = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg',
              capturedAt='2024-07-01T18:30:00-04:00').get_json()
    assert a['capturedAt'] == '2024-07-01T22:30:00+00:00'
    # Attached now, taken then — the two are different facts.
    assert a['createdAt'] != a['capturedAt']


def test_exif_date_is_the_fallback(client, entry_id):
    a = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg').get_json()
    expected = datetime(2026, 3, 14, 9, 41, 2).astimezone(timezone.utc).isoformat()
    assert a['capturedAt'] == expected


def test_no_date_anywhere_is_null(client, entry_id):
    a = _post(client, entry_id, data=_jpeg(taken=None), filename='p.jpg',
              mime='image/jpeg').get_json()
    assert a['capturedAt'] is None


def test_a_malformed_capture_time_is_refused_before_anything_is_stored(client, entry_id):
    r = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg',
              capturedAt='yesterday')
    assert r.status_code == 400
    assert client.get(f'/api/journal/{entry_id}/attachments').get_json() == []


# --- Live Photos ----------------------------------------------------------------

def test_a_live_photo_is_one_attachment_with_its_clip(client, entry_id):
    clip = b'\x00\x00\x00\x14ftypqt  ' + b'\x01' * 4096
    a = _post(client, entry_id, data=_jpeg(), filename='IMG_1.jpg', mime='image/jpeg',
              motion=clip).get_json()
    assert a['kind'] == 'image'
    assert a['hasMotion'] is True
    assert a['motionMime'] == 'video/quicktime'
    assert a['motionSize'] == len(clip)
    assert 'motionPath' not in a
    served = client.get(a['motionUrl'])
    assert served.status_code == 200
    assert served.data == clip
    assert served.mimetype == 'video/quicktime'
    # Range requests, so the phone can stream it.
    ranged = client.get(a['motionUrl'], headers={'Range': 'bytes=0-3'})
    assert ranged.status_code == 206
    assert len(client.get(f'/api/journal/{entry_id}/attachments').get_json()) == 1


def test_a_still_has_no_motion(client, entry_id):
    a = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg').get_json()
    assert a['hasMotion'] is False
    assert 'motionUrl' not in a
    assert client.get(f'/api/journal/attachments/{a["id"]}/motion').status_code == 404


def test_motion_beside_a_non_image_is_refused(client, entry_id):
    r = _post(client, entry_id, data=b'\x00' * 2048, filename='memo.m4a',
              mime='audio/mp4', motion=b'\x00' * 64)
    assert r.status_code == 400
    assert client.get(f'/api/journal/{entry_id}/attachments').get_json() == []


def test_motion_that_is_not_a_video_is_refused(client, entry_id):
    body = {'file': (io.BytesIO(_jpeg()), 'p.jpg', 'image/jpeg'),
            'motion': (io.BytesIO(b'hello'), 'notes.txt', 'text/plain')}
    r = client.post(f'/api/journal/{entry_id}/attachments', data=body,
                    content_type='multipart/form-data')
    assert r.status_code == 400


def test_a_live_photo_replay_stores_nothing_twice(client, entry_id):
    attachment_id = str(ULID())
    first = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg',
                  motion=b'\x01' * 512, attachmentId=attachment_id)
    again = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg',
                  motion=b'\x02' * 512, attachmentId=attachment_id)
    assert first.status_code == again.status_code == 201
    assert again.get_json()['id'] == attachment_id
    assert client.get(again.get_json()['motionUrl']).data == b'\x01' * 512
    assert len(client.get(f'/api/journal/{entry_id}/attachments').get_json()) == 1


def test_deleting_a_live_photo_removes_the_clip(client, entry_id):
    a = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg',
              motion=b'\x01' * 512).get_json()
    directory = storage.attachment_dir(a['id'])
    assert (directory / 'motion.mov').is_file()
    assert client.delete(f'/api/journal/attachments/{a["id"]}').status_code in (200, 204)
    assert not directory.exists()


# --- HEIC -----------------------------------------------------------------------

def test_a_heic_still_becomes_a_jpeg_and_keeps_its_date(client, entry_id):
    pillow_heif = pytest.importorskip('pillow_heif')
    img = Image.new('RGB', (16, 16), (10, 120, 200))
    exif = img.getexif()
    exif.get_ifd(0x8769)[0x9003] = '2025:12:24 20:15:00'
    buf = io.BytesIO()
    pillow_heif.from_pillow(img).save(buf, exif=exif.tobytes())
    a = _post(client, entry_id, data=buf.getvalue(), filename='IMG_2.HEIC',
              mime='image/heic').get_json()
    assert a['mime'] == 'image/jpeg'
    served = client.get(a['url'])
    assert served.data[:2] == b'\xff\xd8'
    expected = datetime(2025, 12, 24, 20, 15).astimezone(timezone.utc).isoformat()
    assert a['capturedAt'] == expected


# --- uploaded video ---------------------------------------------------------------

def test_video_metadata_comes_from_apples_keys():
    probed = journal_video.parse_probe({
        'format': {
            'duration': '14.48',
            'tags': {
                'creation_time': '2024-07-02T01:00:00.000000Z',
                'com.apple.quicktime.creationdate': '2024-07-01T18:30:00-0400',
                'com.apple.quicktime.location.ISO6709': '+43.6532-079.3832+076.000/',
            },
        },
    })
    assert probed['duration_seconds'] == 14
    assert probed['captured_at'] == int(datetime(2024, 7, 1, 22, 30, tzinfo=timezone.utc).timestamp())
    assert probed['latitude'] == pytest.approx(43.6532)
    assert probed['longitude'] == pytest.approx(-79.3832)


def test_video_metadata_falls_back_to_creation_time_and_streams():
    from_format = journal_video.parse_probe(
        {'format': {'tags': {'creation_time': '2024-07-02T01:00:00Z'}}})
    assert from_format['captured_at'] == int(datetime(2024, 7, 2, 1, tzinfo=timezone.utc).timestamp())
    from_stream = journal_video.parse_probe(
        {'format': {}, 'streams': [{'tags': {'creation_time': '2023-01-01T00:00:00Z'}}]})
    assert from_stream['captured_at'] == int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp())


def test_video_metadata_tolerates_nothing():
    assert journal_video.parse_probe({}) == {
        'duration_seconds': None, 'captured_at': None, 'latitude': None, 'longitude': None,
    }
    # A camera with no clock set, and a date with no offset, are not dates.
    assert journal_video.parse_probe(
        {'format': {'tags': {'creation_time': '1970-01-01T00:00:00Z'}}})['captured_at'] is None
    assert journal_video.parse_probe(
        {'format': {'tags': {'com.apple.quicktime.creationdate': '2024-07-01T18:30:00'}}}
    )['captured_at'] is None
    assert journal_video.parse_probe(
        {'format': {'tags': {'location': 'nowhere'}}})['latitude'] is None


def test_a_video_upload_without_ffmpeg_still_lands(client, entry_id, monkeypatch):
    monkeypatch.setattr(journal_video.subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('ffprobe')))
    a = _post(client, entry_id, data=b'\x00' * 4096, filename='clip.mov',
              mime='video/quicktime').get_json()
    assert a['kind'] == 'video'
    assert a['durationSeconds'] is None
    assert 'thumbnailUrl' not in a


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
                    reason='ffmpeg not installed')
def test_a_video_upload_gets_a_poster_and_its_length(client, entry_id, tmp_path):
    clip = tmp_path / 'clip.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
         'testsrc=duration=2:size=320x240:rate=10', '-pix_fmt', 'yuv420p',
         '-metadata', 'creation_time=2024-07-02T01:00:00Z', str(clip)],
        check=True,
    )
    a = _post(client, entry_id, data=clip.read_bytes(), filename='clip.mp4',
              mime='video/mp4').get_json()
    assert a['durationSeconds'] == 2
    assert a['capturedAt'] == '2024-07-02T01:00:00+00:00'
    poster = client.get(a['thumbnailUrl'])
    assert poster.status_code == 200
    assert poster.data[:2] == b'\xff\xd8'


def test_the_phone_is_told_about_posters_and_clips_without_their_paths(client, entry_id):
    from backend.db.connection import get_db
    from backend.mobile_sync import registry
    a = _post(client, entry_id, data=_jpeg(), filename='p.jpg', mime='image/jpeg',
              motion=b'\x01' * 512, capturedAt='2024-07-01T18:30:00-04:00').get_json()
    import json
    row = get_db().execute(
        f'SELECT {registry.payload("journal_attachments", "t")} AS p'
        ' FROM journal_attachments t WHERE id=?', (a['id'],)).fetchone()
    projected = json.loads(row['p'])
    assert projected['has_motion'] == 1
    assert projected['has_thumb'] == 0
    assert projected['captured_at'] == int(datetime(2024, 7, 1, 22, 30, tzinfo=timezone.utc).timestamp())
    assert not any('path' in key for key in projected)


def test_videos_stay_out_of_the_bulk_media_sync():
    from backend.mobile_sync import media
    assert media.active_path('journal_attachments', {'kind': 'video', 'path': '/x'}) is None
