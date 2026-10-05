"""The weather snapshot stored on journal and food entries (backend/weather/entry.py).
No network: backend.weather.fetch.fetch_hourly is replaced per test, and
conftest's no_open_meteo makes any other fetch raise."""
import json
import time

import pytest

from backend.db.connection import get_db
from backend.weather import entry as entry_weather


def _hour(ts, **over):
    hour = {
        'hour_ts': ts, 'weather_code': 2, 'temperature_c': 4.0, 'wet_bulb_c': 2.0,
        'humidity_pct': 80.0, 'apparent_c': -1.5, 'wind_kmh': 22.0, 'gust_kmh': 41.0, 'is_day': 1,
    }
    hour.update(over)
    return hour


@pytest.fixture
def open_meteo(monkeypatch):
    """Hours numbered by how far back they are; records what was asked for."""
    calls = []

    def fake(lat, lon, past_days=1):
        calls.append((lat, lon, past_days))
        base = (int(time.time()) // 3600) * 3600
        return [_hour(base - n * 3600, temperature_c=float(n)) for n in range(24 * past_days, -3, -1)]

    monkeypatch.setattr('backend.weather.fetch.fetch_hourly', fake)
    return calls


@pytest.fixture
def no_default_location():
    db = get_db()
    db.execute('UPDATE settings SET weather_default_lat=NULL, weather_default_lon=NULL')
    db.execute('DELETE FROM lifestyle_weather_locations')
    db.commit()


def _weather(table, entry_id):
    row = get_db().execute(f'SELECT weather FROM {table} WHERE id=?', (entry_id,)).fetchone()
    return json.loads(row['weather']) if row and row['weather'] else None


def _journal(client, **body):
    res = client.post('/api/journal', json={'content': 'An entry', **body})
    assert res.status_code == 201
    return res.get_json()['id']


def test_windy_is_sustained_wind_or_strong_gusts():
    assert not entry_weather.is_windy(None, None)
    assert not entry_weather.is_windy(29, 49)
    assert entry_weather.is_windy(30, 0)
    assert entry_weather.is_windy(10, 50)


def test_the_snapshot_is_for_the_capture_hour_not_the_upload(open_meteo):
    now = time.time()
    # Thirty seconds into the hour that began two hours before this one.
    two_hours_ago = (int(now) // 3600) * 3600 - 2 * 3600 + 30
    found = entry_weather.weather_at(43.6, -79.4, two_hours_ago, now=now)
    assert found['temperatureC'] == 2.0
    assert found == {**found, 'apparentC': -1.5, 'windKmh': 22.0, 'gustKmh': 41.0,
                     'windy': False, 'isDay': True, 'latitude': 43.6, 'longitude': -79.4}


def test_a_late_upload_reaches_further_back_but_not_past_open_meteos_window(open_meteo):
    now = time.time()
    entry_weather.weather_at(1.0, 2.0, int(now) - 50 * 3600, now=now)
    assert open_meteo[-1][2] == 4  # ceil(50h / 24h) + 1
    assert entry_weather.weather_at(1.0, 2.0, int(now) - 93 * 86400, now=now) is None
    assert len(open_meteo) == 1


def test_saving_does_not_fetch_the_sweep_does(client, open_meteo):
    entry_id = _journal(client, latitude=44.0, longitude=-78.5)
    assert open_meteo == [] and _weather('journal_entries', entry_id) is None
    assert entry_weather.sweep() == 1
    weather = _weather('journal_entries', entry_id)
    assert (weather['latitude'], weather['longitude']) == (44.0, -78.5)
    assert weather['apparentC'] == -1.5


def test_an_offline_capture_gets_the_weather_of_its_own_hour(client, open_meteo):
    captured = (int(time.time()) // 3600) * 3600 - 5 * 3600 + 60
    entry_id = _journal(client, latitude=44.0, longitude=-78.5,
                        capturedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(captured)))
    entry_weather.sweep()
    assert _weather('journal_entries', entry_id)['temperatureC'] == 5.0


def test_an_unlocated_new_entry_uses_the_weather_cards_location(client, open_meteo):
    db = get_db()
    db.execute('DELETE FROM lifestyle_weather_locations')
    db.execute('UPDATE settings SET weather_default_lat=43.65, weather_default_lon=-79.38')
    db.commit()
    entry_id = _journal(client)
    entry_weather.sweep()
    assert _weather('journal_entries', entry_id)['latitude'] == 43.65


def test_an_old_unlocated_entry_is_not_given_todays_location(client, open_meteo):
    db = get_db()
    db.execute('UPDATE settings SET weather_default_lat=43.65, weather_default_lon=-79.38')
    db.commit()
    old = _journal(client, capturedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 3 * 86400)))
    located = _journal(client, latitude=1.0, longitude=2.0,
                       capturedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 3 * 86400)))
    entry_weather.sweep()
    assert _weather('journal_entries', old) is None
    # A located entry from the last 92 days is filled in, which is also the backfill.
    assert _weather('journal_entries', located) is not None


def test_with_no_location_anywhere_nothing_is_fetched(client, open_meteo, no_default_location):
    entry_id = _journal(client)
    entry_weather.sweep()
    assert _weather('journal_entries', entry_id) is None
    assert open_meteo == []


def test_a_failed_lookup_is_retried_later_not_every_tick(client, monkeypatch):
    entry_id = _journal(client, latitude=1.0, longitude=2.0)
    # conftest's no_open_meteo: every fetch raises.
    assert entry_weather.sweep() == 0
    calls = []
    monkeypatch.setattr('backend.weather.fetch.fetch_hourly',
                        lambda lat, lon, past_days=1: calls.append(1) or [_hour((int(time.time()) // 3600) * 3600)])
    entry_weather.sweep()
    assert calls == [] and _weather('journal_entries', entry_id) is None
    entry_weather.sweep(now=time.time() + entry_weather.RETRY_AFTER + 60)
    assert calls == [1]


def test_entries_near_each_other_share_one_fetch(client, open_meteo):
    first = _journal(client, latitude=44.001, longitude=-78.5)
    second = _journal(client, latitude=44.002, longitude=-78.5)
    assert entry_weather.sweep() == 2
    assert len(open_meteo) == 1
    assert _weather('journal_entries', first) and _weather('journal_entries', second)


def test_a_meal_gets_weather_too(client, open_meteo):
    res = client.post('/api/food', json={'text': 'Ramen', 'latitude': 45.5, 'longitude': -73.6})
    assert res.status_code == 201
    entry_weather.sweep()
    entry_id = res.get_json()['id']
    assert _weather('food_entries', entry_id)['latitude'] == 45.5
    # The meal's own GET carries it, which is how the phone reads it back.
    assert json.loads(client.get(f'/api/food/{entry_id}').get_json()['weather'])['latitude'] == 45.5


def test_weather_already_recorded_is_not_fetched_again(client, open_meteo):
    _journal(client, latitude=1.0, longitude=2.0)
    entry_weather.sweep()
    entry_weather.sweep(now=time.time() + entry_weather.RETRY_AFTER + 60)
    assert len(open_meteo) == 1


def test_saving_an_entry_wakes_the_sweep(client):
    entry_weather._wake.clear()
    _journal(client)
    assert entry_weather._wake.is_set()


def test_the_phone_replica_carries_the_weather():
    from backend.mobile_sync.registry import COLLECTIONS
    assert 'weather' in COLLECTIONS['journal_entries']
