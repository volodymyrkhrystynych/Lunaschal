"""Project Health into Lifestyle; manual weigh-ins and workout edits win.

Workout links survive deleting a log (NULL session_id), preventing resurrection
on replay/restart. Weight uses the latest sample per 4am day unless corrected.
"""
import json
import math
import re
import time

from ulid import ULID
from backend.apple_health import intensity
from backend.day_boundary import day_key_for

BODY_MASS = 'HKQuantityTypeIdentifierBodyMass'
KG_PER_LB = 0.45359237


def legacy_weight_kg(value: float) -> float:
    """Historical rule: below 140 kg, 140 and above pounds."""
    return value * KG_PER_LB if value >= 140 else value


def health_weight_kg(value, unit):
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    # The phone exports kg, including pound numbers mistakenly entered as kg.
    # Explicit pounds need only the ordinary conversion, never two conversions.
    if unit in ('lb', 'lbs'):
        return value * KG_PER_LB
    if unit == 'g':
        value /= 1000
    elif unit != 'kg':
        return None
    return legacy_weight_kg(value)


def reconcile(db):
    """Run in the caller's transaction at startup and after Health sync."""
    for row in db.execute(
        'SELECT w.* FROM health_workouts w LEFT JOIN health_workout_links l ON l.health_id=w.id'
        ' WHERE l.health_id IS NULL'
    ).fetchall():
        sid = str(ULID())
        metadata = json.loads(row['metadata'] or '{}')
        # A sport cannot establish location: walking may be a treadmill.
        location = 'outside' if metadata.get('HKIndoorWorkout') is False else 'unassigned'
        start, end = int(row['start_ts']), int(row['end_ts'])
        name = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', row['activity_name']).replace('_', ' ').title()
        db.execute(
            'INSERT INTO workout_sessions(id, capture_kind, started_at, ended_at, date,'
            ' location_type, duration_minutes, notes, parse_status, created_at, updated_at)'
            ' VALUES (?,\'health\',?,?,?,?,?,?,\'skipped\',?,?)',
            (sid, start, end, day_key_for(start), location,
             round(min(row['duration_s'], row['end_ts'] - row['start_ts']) / 60),
             f'Apple Health · {name}', start, row['received_at']),
        )
        db.execute('INSERT INTO health_workout_links(health_id, session_id) VALUES (?,?)', (row['id'], sid))

    rate_workouts(db)

    latest = {}
    for row in db.execute('SELECT * FROM health_samples WHERE type=? ORDER BY start_ts, id', (BODY_MASS,)):
        weight = health_weight_kg(row['value'], row['unit'])
        if weight is not None:
            day = day_key_for(row['start_ts'])
            latest[day] = (row, weight)
    for row in db.execute('SELECT id, date FROM body_weight_logs WHERE health_sample_id IS NOT NULL').fetchall():
        if row['date'] not in latest:
            db.execute('DELETE FROM body_weight_logs WHERE id=?', (row['id'],))
    for day, (row, weight) in latest.items():
        db.execute(
            'INSERT INTO body_weight_logs(id,date,weight,weight_unit,health_sample_id,created_at,updated_at)'
            ' VALUES (?,?,?,\'kg\',?,?,?) ON CONFLICT(date) DO UPDATE SET'
            ' weight=excluded.weight, health_sample_id=excluded.health_sample_id, updated_at=excluded.updated_at'
            ' WHERE body_weight_logs.health_sample_id IS NOT NULL AND'
            ' (body_weight_logs.health_sample_id != excluded.health_sample_id OR body_weight_logs.weight != excluded.weight)',
            (str(ULID()), day, weight, row['id'], int(row['start_ts']), row['received_at']),
        )


# Heart-rate samples can reach the server a sync or two after their workout,
# so a derived rating is re-derived for this long before it is left alone.
RERATE_WINDOW_S = 7 * 86400


def rate_workouts(db, *, now=None):
    """Derive stars for imported workouts nobody has rated by hand.

    A rating set by hand is `intensity_source='user'` and never touched; one
    from before the column existed has a value and no source, and is left
    alone too. Only unrated sessions and our own recent ratings are read, so
    the 180-day peak query does not run over the whole history every sync.
    """
    now = time.time() if now is None else now
    rows = db.execute(
        'SELECT w.*, s.id AS session_id, s.intensity_rating, s.intensity_source'
        ' FROM health_workouts w JOIN health_workout_links l ON l.health_id=w.id'
        ' JOIN workout_sessions s ON s.id=l.session_id'
        " WHERE (s.intensity_rating IS NULL AND s.intensity_source IS NULL)"
        " OR (s.intensity_source='health' AND w.end_ts>=?)",
        (now - RERATE_WINDOW_S,),
    ).fetchall()
    for row in rows:
        stars = intensity.rate(db, row)
        if stars is None or stars == row['intensity_rating']:
            continue
        db.execute(
            "UPDATE workout_sessions SET intensity_rating=?, intensity_source='health' WHERE id=?",
            (stars, row['session_id']),
        )


def delete_workouts(db, deleted_ids):
    for (health_id,) in deleted_ids:
        db.execute('DELETE FROM workout_sessions WHERE id IN'
                   ' (SELECT session_id FROM health_workout_links WHERE health_id=?)', (health_id,))
        db.execute('DELETE FROM health_workout_links WHERE health_id=?', (health_id,))
