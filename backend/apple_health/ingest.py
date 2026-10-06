"""Validate and store one batch from the phone.

The phone advances its HealthKit anchor only after this answers, so the batch
contract is shaped around that:

- **Every write is an upsert keyed by HealthKit's own UUID** (or `(date, type)`
  for a daily total), so a batch replayed after a lost reply changes nothing.
- **A bad item is skipped and reported, never a reason to refuse the batch.** A
  400 would leave the anchor where it is, and the phone would resend the same
  batch -- with the same bad item in it -- on every sync forever. Skipping one
  odd sample costs one sample; refusing costs everything after it.
- Only the *shape* of a batch (not a list, too many items) is a 400, because
  that is a bug in the sender rather than something HealthKit produced.
"""
import json
import math
import re
import time

from backend.db.connection import get_db

MAX_SAMPLES = 5000
MAX_WORKOUTS = 1000
MAX_DELETED = 5000
MAX_DAILY = 5000
MAX_METADATA_BYTES = 8192
# Reported back so the phone can log them; past this many the count still holds.
MAX_REPORTED_REJECTIONS = 50

_UUID = re.compile(r'^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$')
_TYPE = re.compile(r'^HK[A-Za-z0-9]{1,120}$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# HealthKit began in 2014; anything well before that, or in the future, is a
# clock or encoding bug rather than a measurement.
_EARLIEST = 946684800  # 2000-01-01
_FUTURE_SLACK = 2 * 86400


class BatchError(ValueError):
    """The batch itself is malformed -- answered with a 400."""


def _text(value, limit: int = 200) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('not a string')
    return value[:limit] or None


def _number(value, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('not a finite number')
    return float(value)


def _interval(item: dict, now: float) -> tuple[float, float]:
    start = _number(item.get('start'))
    end = _number(item.get('end'))
    if end < start:
        raise ValueError('ends before it starts')
    if start < _EARLIEST or end > now + _FUTURE_SLACK:
        raise ValueError('outside any plausible time')
    return start, end


def _uuid(item: dict) -> str:
    value = item.get('uuid')
    if not isinstance(value, str) or not _UUID.match(value):
        raise ValueError('uuid is not a HealthKit UUID')
    return value.upper()


def _type(value) -> str:
    if not isinstance(value, str) or not _TYPE.match(value):
        raise ValueError('type is not a HealthKit identifier')
    return value


def _metadata(value) -> str | None:
    """Kept as JSON for analysis; an oversized blob is dropped, not truncated,
    since half a JSON document is not JSON."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError('metadata is not an object')
    encoded = json.dumps(value, separators=(',', ':'), default=str)
    return encoded if len(encoded) <= MAX_METADATA_BYTES else None


def _list(body: dict, key: str, limit: int) -> list:
    value = body.get(key, [])
    if not isinstance(value, list):
        raise BatchError(f'{key} must be a list')
    if len(value) > limit:
        raise BatchError(f'{key} holds more than {limit} items')
    return value


def ingest(body) -> dict:
    """Store one batch. Returns counts plus the first few rejections."""
    if not isinstance(body, dict):
        raise BatchError('body must be a JSON object')
    samples = _list(body, 'samples', MAX_SAMPLES)
    workouts = _list(body, 'workouts', MAX_WORKOUTS)
    deleted = _list(body, 'deleted', MAX_DELETED)
    daily = _list(body, 'daily', MAX_DAILY)

    now = time.time()
    received = int(now)
    rejected: list[dict] = []
    rejected_count = 0

    def reject(section: str, index: int, reason: str) -> None:
        nonlocal rejected_count
        rejected_count += 1
        if len(rejected) < MAX_REPORTED_REJECTIONS:
            rejected.append({'section': section, 'index': index, 'reason': reason})

    sample_rows = []
    for i, item in enumerate(samples):
        try:
            if not isinstance(item, dict):
                raise ValueError('not an object')
            kind = item.get('kind')
            if kind not in ('quantity', 'category'):
                raise ValueError('kind must be quantity or category')
            start, end = _interval(item, now)
            value = _number(item.get('value'), allow_none=kind == 'category')
            unit = _text(item.get('unit'), 40)
            if kind == 'quantity' and not unit:
                raise ValueError('a quantity needs a unit')
            sample_rows.append((
                _uuid(item), _type(item.get('type')), kind, start, end, value,
                unit if kind == 'quantity' else None,
                _text(item.get('source')), _text(item.get('sourceBundle')),
                _text(item.get('device'), 400), _metadata(item.get('metadata')), received,
            ))
        except ValueError as e:
            reject('samples', i, str(e))

    workout_rows = []
    for i, item in enumerate(workouts):
        try:
            if not isinstance(item, dict):
                raise ValueError('not an object')
            start, end = _interval(item, now)
            activity = item.get('activityType')
            if isinstance(activity, bool) or not isinstance(activity, int) or activity < 0:
                raise ValueError('activityType must be a non-negative integer')
            duration = _number(item.get('duration'))
            if duration < 0:
                raise ValueError('negative duration')
            workout_rows.append((
                _uuid(item), activity, _text(item.get('activityName'), 60) or f'activity-{activity}',
                start, end, duration,
                _number(item.get('energy'), allow_none=True),
                _number(item.get('distance'), allow_none=True),
                _text(item.get('source')), _text(item.get('sourceBundle')),
                _metadata(item.get('metadata')), received,
            ))
        except ValueError as e:
            reject('workouts', i, str(e))

    deleted_ids = []
    for i, item in enumerate(deleted):
        if isinstance(item, str) and _UUID.match(item):
            deleted_ids.append((item.upper(),))
        else:
            reject('deleted', i, 'not a HealthKit UUID')

    daily_rows = []
    for i, item in enumerate(daily):
        try:
            if not isinstance(item, dict):
                raise ValueError('not an object')
            date = item.get('date')
            if not isinstance(date, str) or not _DATE.match(date):
                raise ValueError('date must be YYYY-MM-DD')
            unit = _text(item.get('unit'), 40)
            if not unit:
                raise ValueError('a daily total needs a unit')
            daily_rows.append((date, _type(item.get('type')), _number(item.get('value')), unit, received))
        except ValueError as e:
            reject('daily', i, str(e))

    db = get_db()
    with db:
        db.executemany(
            'INSERT OR REPLACE INTO health_samples(id, type, kind, start_ts, end_ts, value, unit,'
            ' source_name, source_bundle, device, metadata, received_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            sample_rows,
        )
        db.executemany(
            'INSERT OR REPLACE INTO health_workouts(id, activity_type, activity_name, start_ts, end_ts,'
            ' duration_s, energy_kcal, distance_m, source_name, source_bundle, metadata, received_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            workout_rows,
        )
        # A deleted UUID can name either kind of object; HealthKit doesn't say.
        db.executemany('DELETE FROM health_samples WHERE id = ?', deleted_ids)
        db.executemany('DELETE FROM health_workouts WHERE id = ?', deleted_ids)
        db.executemany(
            'INSERT INTO health_daily(date, type, value, unit, updated_at) VALUES (?,?,?,?,?)'
            ' ON CONFLICT(date, type) DO UPDATE SET value=excluded.value, unit=excluded.unit,'
            ' updated_at=excluded.updated_at',
            daily_rows,
        )

    return {
        'samples': len(sample_rows),
        'workouts': len(workout_rows),
        'deleted': len(deleted_ids),
        'daily': len(daily_rows),
        'rejectedCount': rejected_count,
        'rejected': rejected,
    }
