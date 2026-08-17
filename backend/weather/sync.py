"""Idempotent per-(day, hour) weather sync — the newspaper sync's idempotent-
per-(paper, date) pattern (backend/newspapers/sync.py), but upserting instead
of skipping: every resync overwrites elapsed hours with observed conditions
and remaining hours with a fresh forecast, so there's no separate job needed
to flip an hour from forecast to actual as the day passes it.

No daemon loop drives this — the Lifestyle tab's weather card triggers a sync
on mount and again once geolocation resolves (see backend/routes/weather.py),
because a background thread could never capture the device's location anyway.
"""
import time

from ulid import ULID

from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import row_to_dict
from backend.weather import fetch

# A resync five seconds after the last one (e.g. two tab visits in a row)
# shouldn't add a new location-log row — only a fix that actually differs.
_DEDUPE_DECIMALS = 3


def resolve_location(db) -> tuple[float, float, str] | None:
    """(lat, lon, source) to sync with: the most recently logged location
    (any day — yesterday's last-known fix is still better than nothing),
    else the configured default, else None."""
    row = db.execute(
        'SELECT latitude, longitude FROM lifestyle_weather_locations'
        ' ORDER BY created_at DESC LIMIT 1'
    ).fetchone()
    if row:
        return row['latitude'], row['longitude'], 'geolocation'

    settings = db.execute(
        'SELECT weather_default_lat, weather_default_lon FROM settings LIMIT 1'
    ).fetchone()
    if settings and settings['weather_default_lat'] is not None \
            and settings['weather_default_lon'] is not None:
        return settings['weather_default_lat'], settings['weather_default_lon'], 'default'

    return None


def record_location(db, day_key: str, lat: float, lon: float, source: str) -> None:
    last = db.execute(
        'SELECT latitude, longitude FROM lifestyle_weather_locations'
        ' WHERE day_key=? ORDER BY created_at DESC LIMIT 1',
        (day_key,),
    ).fetchone()
    if last and round(last['latitude'], _DEDUPE_DECIMALS) == round(lat, _DEDUPE_DECIMALS) \
            and round(last['longitude'], _DEDUPE_DECIMALS) == round(lon, _DEDUPE_DECIMALS):
        return
    db.execute(
        'INSERT INTO lifestyle_weather_locations(id, day_key, latitude, longitude, source, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (str(ULID()), day_key, lat, lon, source, int(time.time())),
    )
    db.commit()


def sync_day(db, day_key: str, lat: float, lon: float, source: str) -> list[dict]:
    """Fetch and upsert the full day's hours for (lat, lon), returning the
    day's rows in order. Hours already past `now` are marked actual."""
    start, end = day_bounds(day_key)
    hours = fetch.fetch_hourly(lat, lon)
    now = time.time()

    for h in hours:
        if not (start <= h['hour_ts'] < end):
            continue
        is_actual = 1 if h['hour_ts'] <= now else 0
        db.execute(
            """
            INSERT INTO lifestyle_weather_hours
                (id, day_key, hour_ts, weather_code, temperature_c, wet_bulb_c,
                 humidity_pct, is_actual, latitude, longitude, location_source,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(day_key, hour_ts) DO UPDATE SET
                weather_code=excluded.weather_code,
                temperature_c=excluded.temperature_c,
                wet_bulb_c=excluded.wet_bulb_c,
                humidity_pct=excluded.humidity_pct,
                is_actual=excluded.is_actual,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                location_source=excluded.location_source,
                updated_at=excluded.updated_at
            """,
            (
                str(ULID()), day_key, h['hour_ts'], h['weather_code'], h['temperature_c'],
                h['wet_bulb_c'], h['humidity_pct'], is_actual, lat, lon, source,
                int(now), int(now),
            ),
        )
    db.commit()
    return _day_rows(db, day_key)


def _day_rows(db, day_key: str) -> list[dict]:
    rows = db.execute(
        'SELECT * FROM lifestyle_weather_hours WHERE day_key=? ORDER BY hour_ts ASC',
        (day_key,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def ensure_today(db) -> list[dict]:
    """The no-daemon entry point for GET /today: returns today's rows,
    fetching them for the first time this day if none exist yet. A GET must
    not hit Open-Meteo on every render, so an existing day is returned as-is —
    fresh data only comes from a POST /location resync."""
    day_key = day_key_for()
    existing = _day_rows(db, day_key)
    if existing:
        return existing

    location = resolve_location(db)
    if location is None:
        return []
    lat, lon, source = location
    return sync_day(db, day_key, lat, lon, source)
