"""The weather where and when a journal or food entry was written.

Stored on the entry as a JSON snapshot (`journal_entries.weather`,
`food_entries.weather`) by a sweep that runs on its own daemon loop
(`start_entry_weather_scheduler`). Saving an entry wakes the loop, so the
weather lands within seconds; the entry's own NULL column is the queue, so a
restart loses nothing and nothing is mirrored anywhere else. It is not an
`llm_jobs` row: that queue is for model work, runs it one job at a time, and
never retries a failure — a weather lookup is a network call that should
neither wait behind a polish nor give up after one bad minute.

It is looked up for the entry's *capture* hour, not the hour it arrived: an
entry written offline on the phone and uploaded that evening still gets the
morning's weather. The place is the entry's own fix. An unlocated entry uses
the last place the weather card knew (backend/weather/sync.py's
resolve_location), but only while it is less than a day old — using today's
location for a two-month-old entry would be a guess presented as a record.
"""
import json
import logging
import math
import os
import threading
import time

from backend.weather import fetch, sync

logger = logging.getLogger(__name__)

TABLES = ('journal_entries', 'food_entries')

# Environment Canada's wind warning starts far higher; this is the point where
# wind is the thing you notice about the day. Sustained 30 km/h is Beaufort 5
# ("fresh breeze"); gusts of 50 km/h are what pulls at an umbrella.
WINDY_SUSTAINED_KMH = 30
WINDY_GUST_KMH = 50

# An entry whose lookup failed (Open-Meteo down, no such hour yet) is tried
# again after this long, until it falls out of Open-Meteo's window.
RETRY_AFTER = 6 * 3600
UNLOCATED_FOR = 86400
BATCH = 20
IDLE_SECONDS = 600

_wake = threading.Event()


def is_windy(wind_kmh, gust_kmh) -> bool:
    return (wind_kmh or 0) >= WINDY_SUSTAINED_KMH or (gust_kmh or 0) >= WINDY_GUST_KMH


def snapshot(hour: dict, lat: float, lon: float) -> dict:
    """The stored shape. camelCase, since it goes to clients as-is."""
    is_day = hour.get('is_day')
    return {
        'hourTs': hour['hour_ts'],
        'weatherCode': hour['weather_code'],
        'temperatureC': hour['temperature_c'],
        'apparentC': hour.get('apparent_c'),
        'humidityPct': hour.get('humidity_pct'),
        'windKmh': hour.get('wind_kmh'),
        'gustKmh': hour.get('gust_kmh'),
        'windy': is_windy(hour.get('wind_kmh'), hour.get('gust_kmh')),
        'isDay': None if is_day is None else bool(is_day),
        'latitude': lat,
        'longitude': lon,
    }


def _past_days(ts: int, now: float) -> int:
    return math.ceil(max(0, now - ts) / 86400) + 1


def weather_at(lat: float, lon: float, ts: int, *, now: float | None = None, cache: dict | None = None) -> dict | None:
    """The snapshot for the hour containing `ts`, or None when Open-Meteo's
    forecast endpoint cannot reach that far back or has no such hour. `cache`
    lets one sweep share a fetch between entries written near each other."""
    now = time.time() if now is None else now
    days_back = _past_days(ts, now)
    if days_back > fetch.MAX_PAST_DAYS:
        return None
    key = (round(lat, 2), round(lon, 2), days_back)
    hours = cache.get(key) if cache is not None else None
    if hours is None:
        hours = fetch.fetch_hourly(lat, lon, past_days=days_back)
        if cache is not None:
            cache[key] = hours
    for hour in hours:
        if hour['hour_ts'] <= ts < hour['hour_ts'] + 3600:
            return snapshot(hour, lat, lon)
    return None


def nudge() -> None:
    """Called after an entry is saved: look its weather up now rather than at
    the next idle tick. Never fails the save."""
    _wake.set()


def sweep(*, now: float | None = None, limit: int = BATCH) -> int:
    """Fill in weather for entries that lack it. Returns how many were filled.
    Stops at the first fetch that fails: when Open-Meteo is down, every other
    entry would fail the same way."""
    from backend.db.connection import get_db

    now = time.time() if now is None else now
    db = get_db()
    known = sync.resolve_location(db)
    cache: dict = {}
    filled = 0
    for table in TABLES:
        rows = db.execute(
            f'SELECT id, created_at, latitude, longitude FROM {table}'
            ' WHERE weather IS NULL AND created_at >= ?'
            ' AND (weather_checked_at IS NULL OR weather_checked_at < ?)'
            ' AND ((latitude IS NOT NULL AND longitude IS NOT NULL) OR created_at >= ?)'
            ' ORDER BY created_at DESC LIMIT ?',
            (int(now - fetch.MAX_PAST_DAYS * 86400), int(now - RETRY_AFTER), int(now - UNLOCATED_FOR), limit),
        ).fetchall()
        for row in rows:
            if row['latitude'] is not None and row['longitude'] is not None:
                lat, lon = row['latitude'], row['longitude']
            elif known is not None:
                lat, lon, _ = known
            else:
                continue
            try:
                found = weather_at(lat, lon, row['created_at'], now=now, cache=cache)
            except Exception as e:
                db.execute(f'UPDATE {table} SET weather_checked_at=? WHERE id=?', (int(now), row['id']))
                db.commit()
                logger.info('Entry weather lookup failed, retrying later: %s', e)
                return filled
            db.execute(
                f'UPDATE {table} SET weather=COALESCE(weather, ?), weather_checked_at=? WHERE id=?',
                (json.dumps(found) if found else None, int(now), row['id']),
            )
            db.commit()
            filled += found is not None
    return filled


def _loop() -> None:
    while True:
        try:
            # A full batch means more are waiting; go again without sleeping.
            if sweep() >= BATCH:
                continue
        except Exception as e:
            logger.warning('Entry weather sweep failed: %s', e)
        _wake.wait(IDLE_SECONDS)
        _wake.clear()


def start_entry_weather_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_loop, daemon=True, name='entry-weather').start()
