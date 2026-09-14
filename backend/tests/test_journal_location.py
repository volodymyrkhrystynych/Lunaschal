"""Where a journal entry was written, and what inherits it.

The composer asks for the device fix explicitly (a button, not a silent grab on
submit) because it is the *only* location a journal photo usually has: iOS
strips GPS EXIF from a picture taken through the browser's camera input, so
every attachment on such an entry reads as unlocated unless the entry itself
was located.

So the behaviours pinned here are: the create route stores a pair and rejects
half of one; a photo with its own EXIF keeps it; a photo (or a clip) without
one inherits the entry's; and a create that lands on a row the recording route
already made still locates it.
"""
import io

import pytest
from PIL import Image
from ulid import ULID

from backend.db.connection import get_db
from backend.routes import journal as journal_routes

TORONTO = (43.6532, -79.3832)


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg', '_transcribe_attachment_bg',
                 '_describe_attachment_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


def _jpeg(gps=None):
    """A tiny JPEG, optionally carrying a GPS fix of its own."""
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    exif = img.getexif()
    if gps:
        lat_ref, lat, lon_ref, lon = gps
        g = exif.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = lat_ref, lat, lon_ref, lon
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _create(client, **body):
    return client.post('/api/journal', json={'content': 'A day.', **body})


def _upload(client, entry_id, *, filename='memo.m4a', data=b'\x00' * 2048,
            mime='audio/mp4'):
    return client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'file': (io.BytesIO(data), filename, mime)},
        content_type='multipart/form-data',
    )


def _entry(client, entry_id):
    return client.get(f'/api/journal/{entry_id}').get_json()


def test_create_stores_the_coordinates_it_was_given(client):
    lat, lon = TORONTO
    id = _create(client, latitude=lat, longitude=lon).get_json()['id']
    entry = _entry(client, id)
    assert entry['latitude'] == pytest.approx(lat)
    assert entry['longitude'] == pytest.approx(lon)


def test_create_without_coordinates_leaves_them_null(client):
    """The ask is explicit — most entries are saved without pressing it."""
    id = _create(client).get_json()['id']
    entry = _entry(client, id)
    assert entry['latitude'] is None
    assert entry['longitude'] is None


def test_half_a_location_is_not_stored(client):
    """`coord_pair`'s rule: a lone latitude would be a row that looks located
    and isn't."""
    id = _create(client, latitude=TORONTO[0]).get_json()['id']
    entry = _entry(client, id)
    assert entry['latitude'] is None
    assert entry['longitude'] is None


def test_unusable_coordinates_are_dropped_rather_than_stored(client):
    id = _create(client, latitude='NaN', longitude=TORONTO[1]).get_json()['id']
    assert _entry(client, id)['latitude'] is None


def test_camera_photo_inherits_the_entry_location(client):
    """The case the button exists for: a picture taken through the browser's
    camera carries no GPS EXIF, so the entry's fix is the only one there is."""
    lat, lon = TORONTO
    id = _create(client, latitude=lat, longitude=lon).get_json()['id']
    a = _upload(client, id, filename='snap.jpg', mime='image/jpeg',
                data=_jpeg()).get_json()
    assert a['latitude'] == pytest.approx(lat)
    assert a['longitude'] == pytest.approx(lon)


def test_photo_exif_wins_over_the_entry_location(client):
    """EXIF says where the picture was taken; the entry's fix only says where
    it was written. A photo picked out of the library keeps its own."""
    id = _create(client, latitude=10.0, longitude=10.0).get_json()['id']
    a = _upload(
        client, id, filename='view.jpg', mime='image/jpeg',
        data=_jpeg(gps=('N', (43.0, 39.0, 11.0), 'W', (79.0, 22.0, 59.0))),
    ).get_json()
    assert a['latitude'] == pytest.approx(43.653056)
    assert a['longitude'] == pytest.approx(-79.383056)


def test_voice_clip_inherits_the_entry_location(client):
    """Not images only: a clip recorded on the spot was recorded somewhere and
    has no EXIF to read."""
    lat, lon = TORONTO
    id = _create(client, latitude=lat, longitude=lon).get_json()['id']
    a = _upload(client, id).get_json()
    assert a['latitude'] == pytest.approx(lat)
    assert a['longitude'] == pytest.approx(lon)


def test_attachment_on_an_unlocated_entry_stays_unlocated(client):
    id = _create(client).get_json()['id']
    a = _upload(client, id).get_json()
    assert a['latitude'] is None
    assert a['longitude'] is None


def test_create_locates_a_row_the_recording_route_made_first(client):
    """A composer mints its entry id at the first recorded chunk, so the clip's
    row can land before the create does. The create fills that row in — and has
    to carry the location across with the text, or an entry that was dictated
    loses the fix its composer asked for."""
    id = str(ULID())
    # What the recording route leaves behind: the row, empty, waiting for the
    # words that were typed alongside the clip.
    journal_routes.create_journal_entry('', None, 1_700_000_000, entry_id=id)
    lat, lon = TORONTO
    client.post('/api/journal',
                json={'id': id, 'content': 'Said out loud.',
                      'latitude': lat, 'longitude': lon})
    entry = _entry(client, id)
    assert entry['content'] == 'Said out loud.'
    assert entry['latitude'] == pytest.approx(lat)


def test_a_replayed_create_does_not_erase_an_existing_location(client):
    """An offline queue can replay a create. The second one carries no fix (the
    composer has been reset), and COALESCE is what keeps the first one's."""
    lat, lon = TORONTO
    id = _create(client, latitude=lat, longitude=lon).get_json()['id']
    client.post('/api/journal', json={'id': id, 'content': 'A day.'})
    assert _entry(client, id)['latitude'] == pytest.approx(lat)


def test_migration_adds_the_columns_to_an_existing_journal_table():
    """`_ensure_journal_entry_location` is an idempotent guarded ALTER, the
    pattern every column migration in connection.py follows."""
    db = get_db()
    cols = {r[1] for r in db.execute('PRAGMA table_info(journal_entries)')}
    assert {'latitude', 'longitude'} <= cols
    # Running it again on a table that already has them is a no-op.
    from backend.db.connection import _ensure_journal_entry_location
    _ensure_journal_entry_location(db)
    _ensure_journal_entry_location(db)
