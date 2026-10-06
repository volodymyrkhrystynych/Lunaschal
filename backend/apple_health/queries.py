"""Reading the Health mirror back out: for the Lifestyle card, and raw for analysis.

Rows come back with unix-second numbers (`start`, `end`) rather than through
row_to_dict's ISO conversion -- this is data to compute on, and a float second
survives a round trip through pandas or a spreadsheet where a string doesn't.
"""
import json
from datetime import date, timedelta

from backend.day_boundary import day_bounds
from backend.db.connection import get_db

EXERCISE = 'HKQuantityTypeIdentifierAppleExerciseTime'
STEPS = 'HKQuantityTypeIdentifierStepCount'
ACTIVE_ENERGY = 'HKQuantityTypeIdentifierActiveEnergyBurned'


def _metadata(raw: str | None):
    return json.loads(raw) if raw else None


def sample_dict(r) -> dict:
    return {
        'id': r['id'], 'type': r['type'], 'kind': r['kind'],
        'start': r['start_ts'], 'end': r['end_ts'],
        'value': r['value'], 'unit': r['unit'],
        'source': r['source_name'], 'sourceBundle': r['source_bundle'],
        'device': r['device'], 'metadata': _metadata(r['metadata']),
    }


def workout_dict(r) -> dict:
    return {
        'id': r['id'], 'activityType': r['activity_type'], 'activityName': r['activity_name'],
        'start': r['start_ts'], 'end': r['end_ts'], 'durationSeconds': r['duration_s'],
        'energyKcal': r['energy_kcal'], 'distanceMeters': r['distance_m'],
        'source': r['source_name'], 'sourceBundle': r['source_bundle'],
        'metadata': _metadata(r['metadata']),
    }


def window(first_day: str, last_day: str) -> tuple[int, int]:
    """Unix bounds covering two day keys inclusive, on the 4am boundary."""
    return day_bounds(first_day)[0], day_bounds(last_day)[1]


def catalog() -> list[dict]:
    """Every type held, with its span -- the index someone analysing starts from."""
    db = get_db()
    rows = db.execute(
        'SELECT type, kind, unit, COUNT(*) AS n, MIN(start_ts) AS first, MAX(end_ts) AS last'
        ' FROM health_samples GROUP BY type, kind, unit ORDER BY type'
    ).fetchall()
    out = [{'type': r['type'], 'kind': r['kind'], 'unit': r['unit'], 'count': r['n'],
            'first': r['first'], 'last': r['last']} for r in rows]
    w = db.execute('SELECT COUNT(*) AS n, MIN(start_ts) AS first, MAX(end_ts) AS last FROM health_workouts').fetchone()
    if w['n']:
        out.append({'type': 'HKWorkoutType', 'kind': 'workout', 'unit': None, 'count': w['n'],
                    'first': w['first'], 'last': w['last']})
    return out


def samples(type_: str, start: float, end: float, limit: int):
    return get_db().execute(
        'SELECT * FROM health_samples WHERE type = ? AND start_ts >= ? AND start_ts < ?'
        ' ORDER BY start_ts LIMIT ?',
        (type_, start, end, limit),
    )


def workouts(start: float, end: float, limit: int):
    return get_db().execute(
        'SELECT * FROM health_workouts WHERE start_ts >= ? AND start_ts < ?'
        ' ORDER BY start_ts DESC LIMIT ?',
        (start, end, limit),
    )


def daily(types: list[str], first_day: str, last_day: str) -> list[dict]:
    marks = ','.join('?' * len(types))
    rows = get_db().execute(
        f'SELECT date, type, value, unit FROM health_daily WHERE type IN ({marks})'
        ' AND date >= ? AND date <= ? ORDER BY date, type',
        (*types, first_day, last_day),
    ).fetchall()
    return [dict(r) for r in rows]


def activity(today: str, days: int) -> dict:
    """The Lifestyle card: exercise minutes, steps and active energy per day,
    every day emitted including the empty ones (a bar chart that skips a day
    draws a week as shorter than it was), plus the period's workouts."""
    first = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
    by_day: dict[str, dict] = {}
    for row in daily([EXERCISE, STEPS, ACTIVE_ENERGY], first, today):
        by_day.setdefault(row['date'], {})[row['type']] = row['value']
    series = []
    for i in range(days):
        key = (date.fromisoformat(first) + timedelta(days=i)).isoformat()
        values = by_day.get(key, {})
        series.append({
            'date': key,
            'exerciseMinutes': values.get(EXERCISE),
            'steps': values.get(STEPS),
            'activeEnergyKcal': values.get(ACTIVE_ENERGY),
        })
    start, end = window(first, today)
    db = get_db()
    synced = db.execute(
        'SELECT MAX(t) AS t FROM (SELECT MAX(received_at) AS t FROM health_samples'
        ' UNION ALL SELECT MAX(received_at) FROM health_workouts'
        ' UNION ALL SELECT MAX(updated_at) FROM health_daily)'
    ).fetchone()['t']
    return {
        'days': series,
        'workouts': [workout_dict(r) for r in workouts(start, end, 50)],
        'lastSyncedAt': synced,
    }
