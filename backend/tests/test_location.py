"""The location trail: batch ingest, the trail geometry, the day picture and
the Journal's list of mapped days. Tiles are off throughout (LOCATION_TILE_URL
empty) -- nothing here may reach the network."""
import io
from datetime import datetime

import pytest
from PIL import Image

from backend.db.connection import get_db
from backend.location import days, ingest, render, trail
from backend.location.trail import Fix

DAY = '2026-07-08'


def at(date: str, hhmm: str) -> float:
    return datetime.fromisoformat(f'{date}T{hhmm}:00').timestamp()


def point(pid: str, ts: float, lat=43.6532, lon=-79.3832, kind='fine', **extra) -> dict:
    return {'id': pid, 'ts': ts, 'latitude': lat, 'longitude': lon, 'kind': kind, 'accuracy': 10, **extra}


@pytest.fixture(autouse=True)
def _no_tiles(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCATION_TILE_URL', '')
    monkeypatch.setenv('LOCATION_ROOT', str(tmp_path / 'location'))


def count() -> int:
    return get_db().execute('SELECT COUNT(*) c FROM location_points').fetchone()['c']


# --- ingest ---

def test_a_replayed_batch_changes_nothing():
    batch = {'points': [point('fix-00000001', at(DAY, '09:00')), point('fix-00000002', at(DAY, '09:01'))]}
    assert ingest.ingest(batch)['points'] == 2
    assert ingest.ingest(batch)['points'] == 2
    assert count() == 2


def test_a_visit_departure_overwrites_its_arrival():
    arrive = at(DAY, '10:00')
    ingest.ingest({'points': [point('visit-1783000000', arrive, kind='visit')]})
    ingest.ingest({'points': [point('visit-1783000000', arrive, kind='visit', departed=at(DAY, '11:30'))]})
    row = get_db().execute('SELECT * FROM location_points').fetchone()
    assert count() == 1 and row['departed_ts'] == at(DAY, '11:30')


def test_points_are_filed_under_their_4am_day():
    ingest.ingest({'points': [point('fix-latenight', at('2026-07-09', '02:30'))]})
    assert get_db().execute('SELECT day_key FROM location_points').fetchone()['day_key'] == DAY


def test_a_bad_point_is_skipped_not_a_refused_batch():
    result = ingest.ingest({'points': [
        point('fix-goodgood', at(DAY, '09:00')),
        point('fix-badlat01', at(DAY, '09:00'), lat=123),
        point('fix-invalid1', at(DAY, '09:00'), accuracy=-1),
        point('bad', at(DAY, '09:00')),
        point('fix-badkind1', at(DAY, '09:00'), kind='teleport'),
        point('fix-wayback1', 100.0),
        point('visit-backwards', at(DAY, '09:00'), kind='visit', departed=at(DAY, '08:00')),
        'not a point',
    ]})
    assert result['points'] == 1 and result['rejectedCount'] == 7
    assert [r['index'] for r in result['rejected']] == [1, 2, 3, 4, 5, 6, 7]


def test_only_the_batch_shape_is_a_400(client):
    assert client.post('/api/location/points', json={'points': 3}).status_code == 400
    assert client.post('/api/location/points', json=[1]).status_code == 400
    res = client.post('/api/location/points', json={'points': [point('fix-viaroute', at(DAY, '09:00'))]})
    assert res.status_code == 200 and res.get_json() == {'points': 1, 'rejectedCount': 0, 'rejected': []}


# --- trail geometry ---

def test_a_gps_spike_is_dropped_but_a_real_journey_is_kept():
    home = Fix(0, 43.65, -79.38)
    spike = Fix(10, 43.75, -79.38)  # 11 km in ten seconds, then straight back
    back = Fix(20, 43.6501, -79.38)
    assert trail.clean([home, spike, back]) == [home, back]
    far = Fix(3600, 43.75, -79.38)  # the same 11 km in an hour is a trip
    assert trail.clean([home, far]) == [home, far]


def test_inaccurate_fixes_are_dropped_by_kind():
    fine_bad = Fix(0, 43.65, -79.38, 'fine', accuracy=200)
    coarse_ok = Fix(1, 43.65, -79.38, 'coarse', accuracy=200)
    assert trail.clean([fine_bad, coarse_ok]) == [coarse_ok]


def test_only_consecutive_fine_fixes_are_a_track():
    a, b = Fix(0, 43.65, -79.38), Fix(60, 43.651, -79.38)
    gap = Fix(60 + trail.TRACK_GAP_S + 1, 43.652, -79.38)
    coarse = Fix(gap.ts + 60, 43.66, -79.38, 'coarse')
    assert [s.style for s in trail.segments([a, b, gap, coarse])] == ['track', 'jump', 'jump']
    assert trail.tracked_m([a, b, gap]) == pytest.approx(trail.distance_m(a, b))


def test_places_need_a_real_dwell_and_count_an_ongoing_visit():
    short = Fix(0, 1, 1, 'visit', departed=60)
    long = Fix(100, 1, 1, 'visit', departed=100 + trail.MIN_DWELL_S)
    ongoing = Fix(1000, 1, 1, 'visit')
    assert trail.places([short, long, ongoing], now=1000 + trail.MIN_DWELL_S) == [long, ongoing]


# --- rendering ---

def test_zoom_fits_the_whole_day_in_frame():
    walk = [(43.65, -79.38), (43.66, -79.37)]
    commute = [(43.65, -79.38), (43.85, -79.0)]
    assert render.fit_zoom(walk) > render.fit_zoom(commute)
    assert render.fit_zoom([(43.65, -79.38)]) == render.MAX_ZOOM


def test_renders_over_tiles_and_without_them():
    fixes = [Fix(0, 43.65, -79.38), Fix(60, 43.652, -79.381), Fix(4000, 43.66, -79.37, 'visit', departed=8000)]
    asked = []

    def red_tile(z, x, y):
        asked.append((z, x, y))
        return Image.new('RGB', (256, 256), (255, 0, 0))

    for fetch in (red_tile, lambda z, x, y: None):
        png = render.render(fixes, trail.segments(fixes), trail.places(fixes, 9000), [(43.655, -79.375)], fetch)
        image = Image.open(io.BytesIO(png))
        assert image.size == render.SIZE
    assert asked, 'the renderer never asked for a tile'
    # Washed-out red tile behind, untouched in a corner the trail doesn't reach.
    corner = Image.open(io.BytesIO(render.render(fixes, [], [], [], red_tile))).getpixel((5, 5))
    assert corner[0] > 200 and corner[1] < 120


# --- the Journal's days ---

def _seed_day():
    ingest.ingest({'points': [
        point('fix-00000001', at(DAY, '09:00')),
        point('fix-00000002', at(DAY, '09:02'), lat=43.655),
        point('visit-00000001', at(DAY, '10:00'), lat=43.66, kind='visit', departed=at(DAY, '12:00')),
    ]})


def _entry(entry_id: str, ts: float, lat=43.657, lon=-79.38):
    get_db().execute(
        'INSERT INTO journal_entries (id, content, latitude, longitude, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?)', (entry_id, 'x', lat, lon, int(ts), int(ts)))
    get_db().commit()


def test_journal_lists_days_with_a_trail_and_their_entries():
    _seed_day()
    _entry('E1', at(DAY, '11:00'))
    _entry('E2', at('2026-07-01', '11:00'))  # located, but no trail that day: no card
    cards = days.journal_days()
    assert [c['date'] for c in cards] == [DAY]
    card = cards[0]
    assert (card['pointCount'], card['placeCount'], card['entryCount']) == (3, 1, 1)
    # The day's last second, so the card reads as the day's summary.
    assert datetime.fromisoformat(card['archivedAt']).timestamp() == at('2026-07-09', '04:00') - 1
    assert card['imageUrl'].startswith(f'/api/location/days/{DAY}/map.png?v=')


def test_the_picture_is_redrawn_when_its_inputs_change(client):
    _seed_day()
    first = days.journal_days()[0]['imageUrl']
    res = client.get(first)
    assert res.status_code == 200 and res.mimetype == 'image/png'
    assert 'immutable' in res.headers['Cache-Control']

    _entry('E1', at(DAY, '11:00'))
    second = days.journal_days()[0]['imageUrl']
    assert second != first
    assert 'immutable' not in client.get(first).headers['Cache-Control']
    assert client.get(second).status_code == 200
    # Only the current version is kept on disk.
    from backend.location.storage import location_root
    assert len(list((location_root() / 'maps').glob(f'{DAY}-*.png'))) == 1


def test_a_day_with_no_trail_has_no_picture(client):
    assert client.get(f'/api/location/days/{DAY}/map.png').status_code == 404
    assert client.get('/api/location/days/nonsense/map.png').status_code == 400
