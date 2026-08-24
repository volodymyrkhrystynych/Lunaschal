"""Sync + route tests for the Lifestyle weather card. No real network calls:
backend.weather.sync.fetch.fetch_hourly is monkeypatched, following the
newspaper sync tests' pattern (test_newspapers.py)."""
from datetime import datetime

import pytest

from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db
from backend.weather import sync


def _fake_hours(start: int, end: int) -> list[dict]:
    """One hour-row per hour in [start - 4h, end + 4h), so a sync always has
    hours to filter down to the requested day's own window."""
    hours = []
    ts = start - 4 * 3600
    while ts < end + 4 * 3600:
        hours.append({
            'hour_ts': ts,
            'weather_code': 0,
            'temperature_c': 15.0,
            'wet_bulb_c': 10.0,
            'humidity_pct': 60.0,
        })
        ts += 3600
    return hours


_FAKE_SUNRISE_TS = 1000
_FAKE_SUNSET_TS = 2000


@pytest.fixture
def mock_fetch(monkeypatch):
    calls = {'n': 0}

    def fake_fetch_hourly(lat, lon):
        calls['n'] += 1
        day_key = day_key_for()
        start, end = day_bounds(day_key)
        return _fake_hours(start, end)

    def fake_fetch_sun_times(lat, lon):
        return {day_key_for(): {'sunrise_ts': _FAKE_SUNRISE_TS, 'sunset_ts': _FAKE_SUNSET_TS}}

    monkeypatch.setattr('backend.weather.sync.fetch.fetch_hourly', fake_fetch_hourly)
    monkeypatch.setattr('backend.weather.sync.fetch.fetch_sun_times', fake_fetch_sun_times)
    return calls


def _raw_hours(db, day_key):
    return db.execute(
        'SELECT hour_ts, is_actual, temperature_c FROM lifestyle_weather_hours'
        ' WHERE day_key=? ORDER BY hour_ts ASC',
        (day_key,),
    ).fetchall()


def test_sync_day_upserts_exactly_the_day_window(client, mock_fetch):
    db = get_db()
    day_key = day_key_for()
    start, end = day_bounds(day_key)

    sync.sync_day(db, day_key, 43.6, -79.4, 'geolocation')

    rows = _raw_hours(db, day_key)
    assert len(rows) == 24
    assert all(start <= r['hour_ts'] < end for r in rows)


def test_sync_day_marks_is_actual_relative_to_now(client, mock_fetch, monkeypatch):
    db = get_db()
    day_key = day_key_for()
    start, _end = day_bounds(day_key)
    fixed_now = start + 5 * 3600 + 1  # 5 hours + a second into the day

    monkeypatch.setattr('backend.weather.sync.time.time', lambda: fixed_now)
    sync.sync_day(db, day_key, 43.6, -79.4, 'geolocation')

    rows = _raw_hours(db, day_key)
    actual = [r for r in rows if r['is_actual']]
    forecast = [r for r in rows if not r['is_actual']]
    assert len(actual) == 6  # hours 0..5 inclusive have passed
    assert len(forecast) == 18
    assert all(r['hour_ts'] <= fixed_now for r in actual)
    assert all(r['hour_ts'] > fixed_now for r in forecast)


def test_resync_overwrites_rather_than_duplicates(client, mock_fetch):
    db = get_db()
    day_key = day_key_for()

    sync.sync_day(db, day_key, 43.6, -79.4, 'geolocation')
    first_count = len(_raw_hours(db, day_key))

    sync.sync_day(db, day_key, 10.0, 10.0, 'geolocation')
    rows = _raw_hours(db, day_key)
    assert len(rows) == first_count  # no duplicate rows

    located = db.execute(
        'SELECT DISTINCT latitude, longitude FROM lifestyle_weather_hours WHERE day_key=?',
        (day_key,),
    ).fetchall()
    assert len(located) == 1
    assert located[0]['latitude'] == 10.0


def test_resolve_location_fallback_chain(client):
    db = get_db()
    day_key = day_key_for()

    # Nothing configured at all.
    assert sync.resolve_location(db) is None

    # Default settings only.
    client.patch('/api/settings/ai', json={'weatherDefaultLat': 1.0, 'weatherDefaultLon': 2.0})
    lat, lon, source = sync.resolve_location(db)
    assert (lat, lon, source) == (1.0, 2.0, 'default')

    # A logged location wins over the default.
    sync.record_location(db, day_key, 3.0, 4.0, 'geolocation')
    lat, lon, source = sync.resolve_location(db)
    assert (lat, lon, source) == (3.0, 4.0, 'geolocation')


def test_record_location_dedupes_identical_fixes(client):
    db = get_db()
    day_key = day_key_for()

    sync.record_location(db, day_key, 43.6532, -79.3832, 'geolocation')
    sync.record_location(db, day_key, 43.65321, -79.38321, 'geolocation')  # same, rounded
    count = db.execute(
        'SELECT COUNT(*) AS n FROM lifestyle_weather_locations WHERE day_key=?', (day_key,)
    ).fetchone()['n']
    assert count == 1

    sync.record_location(db, day_key, 44.0, -80.0, 'geolocation')  # materially different
    count = db.execute(
        'SELECT COUNT(*) AS n FROM lifestyle_weather_locations WHERE day_key=?', (day_key,)
    ).fetchone()['n']
    assert count == 2


def test_get_today_on_empty_db(client):
    resp = client.get('/api/lifestyle/weather/today')
    assert resp.status_code == 200
    assert resp.get_json() == {
        'hours': [], 'location': None, 'sunriseTs': None, 'sunsetTs': None,
    }


def test_get_today_does_not_refetch_once_synced(client, mock_fetch):
    client.patch('/api/settings/ai', json={'weatherDefaultLat': 1.0, 'weatherDefaultLon': 2.0})
    resp1 = client.get('/api/lifestyle/weather/today')
    assert len(resp1.get_json()['hours']) == 24
    resp2 = client.get('/api/lifestyle/weather/today')
    assert len(resp2.get_json()['hours']) == 24
    assert mock_fetch['n'] == 1


def test_post_location_requires_both_coords(client):
    assert client.post('/api/lifestyle/weather/location', json={'latitude': 43.6}).status_code == 400
    assert client.post('/api/lifestyle/weather/location', json={}).status_code == 400
    assert client.post(
        '/api/lifestyle/weather/location', json={'latitude': 'nan', 'longitude': -79.4}
    ).status_code == 400


def test_post_location_syncs_and_logs(client, mock_fetch):
    resp = client.post(
        '/api/lifestyle/weather/location', json={'latitude': 43.6532, 'longitude': -79.3832}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['hours']) == 24
    assert body['location'] == {'latitude': 43.6532, 'longitude': -79.3832, 'source': 'geolocation'}

    db = get_db()
    day_key = day_key_for()
    logged = db.execute(
        'SELECT COUNT(*) AS n FROM lifestyle_weather_locations WHERE day_key=?', (day_key,)
    ).fetchone()['n']
    assert logged == 1


def test_sync_sun_times_upserts_by_day_key(client, mock_fetch):
    db = get_db()
    day_key = day_key_for()

    sync.sync_sun_times(db, day_key, 43.6, -79.4, 'geolocation')
    rows = db.execute(
        'SELECT * FROM lifestyle_weather_days WHERE day_key=?', (day_key,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]['sunrise_ts'] == _FAKE_SUNRISE_TS
    assert rows[0]['latitude'] == 43.6

    sync.sync_sun_times(db, day_key, 10.0, 10.0, 'geolocation')
    rows = db.execute(
        'SELECT * FROM lifestyle_weather_days WHERE day_key=?', (day_key,)
    ).fetchall()
    assert len(rows) == 1  # overwritten, not duplicated
    assert rows[0]['latitude'] == 10.0


def test_get_today_includes_sun_times(client, mock_fetch):
    client.patch('/api/settings/ai', json={'weatherDefaultLat': 1.0, 'weatherDefaultLon': 2.0})
    resp = client.get('/api/lifestyle/weather/today')
    body = resp.get_json()
    assert body['sunriseTs'] is not None
    assert body['sunsetTs'] is not None


def test_ensure_today_backfills_sun_times_when_missing(client, mock_fetch):
    """A day synced before this feature shipped (or whose sun-times fetch
    previously failed) has hours but no lifestyle_weather_days row —
    ensure_today should backfill sun times without refetching the hours."""
    db = get_db()
    day_key = day_key_for()

    # sync_day alone (unlike ensure_today) never touches lifestyle_weather_days,
    # so this reproduces "hours exist, sun times don't" exactly.
    sync.record_location(db, day_key, 43.6, -79.4, 'geolocation')
    sync.sync_day(db, day_key, 43.6, -79.4, 'geolocation')
    assert sync._day_sun_times(db, day_key) is None

    hours_out, sun = sync.ensure_today(db)
    assert len(hours_out) == 24
    assert sun is not None
    assert sun['sunriseTs'] is not None


def test_fetch_hourly_parses_response(monkeypatch):
    from backend.weather import fetch

    payload = {
        'hourly': {
            'time': ['2026-08-17T00:00', '2026-08-17T01:00'],
            'temperature_2m': [12.5, 13.0],
            'relative_humidity_2m': [80, 78],
            'weather_code': [0, 3],
            'wet_bulb_temperature_2m': [9.0, 9.5],
        }
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr('requests.get', lambda *a, **kw: FakeResponse())

    hours = fetch.fetch_hourly(43.6532, -79.3832)
    assert len(hours) == 2
    assert hours[0]['weather_code'] == 0
    assert hours[0]['temperature_c'] == 12.5
    assert hours[0]['wet_bulb_c'] == 9.0
    assert hours[0]['humidity_pct'] == 80
    expected_ts = int(datetime.fromisoformat('2026-08-17T00:00').timestamp())
    assert hours[0]['hour_ts'] == expected_ts


def test_fetch_sun_times_parses_response(monkeypatch):
    from backend.weather import fetch

    payload = {
        'daily': {
            'time': ['2026-08-16', '2026-08-17', '2026-08-18'],
            'sunrise': ['2026-08-16T06:10', '2026-08-17T06:11', '2026-08-18T06:12'],
            'sunset': ['2026-08-16T20:30', '2026-08-17T20:29', '2026-08-18T20:27'],
        }
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr('requests.get', lambda *a, **kw: FakeResponse())

    sun_map = fetch.fetch_sun_times(43.6532, -79.3832)
    assert set(sun_map.keys()) == {'2026-08-16', '2026-08-17', '2026-08-18'}
    expected_sunrise = int(datetime.fromisoformat('2026-08-17T06:11').timestamp())
    expected_sunset = int(datetime.fromisoformat('2026-08-17T20:29').timestamp())
    assert sun_map['2026-08-17']['sunrise_ts'] == expected_sunrise
    assert sun_map['2026-08-17']['sunset_ts'] == expected_sunset
