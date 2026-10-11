"""Validate and store one batch of the phone's location trail.

The same contract as backend/apple_health/ingest.py, for the same reason: the
phone drops points from its outbox only once this answers, so

- **every write is an upsert keyed by the phone's own id**, which makes a
  replayed batch a no-op and lets a visit's departure overwrite its arrival;
- **a bad point is skipped and reported, never a reason to refuse the batch** --
  a 400 would leave the point in the outbox and resend it forever;
- only the batch's *shape* is a 400, because that is a bug in the sender.
"""
import math
import re
import time

from backend.day_boundary import day_key_for
from backend.db.connection import get_db

MAX_POINTS = 5000
MAX_REPORTED_REJECTIONS = 50
KINDS = ('fine', 'coarse', 'visit')

_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
_EARLIEST = 1262304000  # 2010-01-01: before any phone that runs this app
_FUTURE_SLACK = 2 * 86400


class BatchError(ValueError):
    """The batch itself is malformed -- answered with a 400."""


def _number(value, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('not a finite number')
    return float(value)


def _time(value, now: float, *, allow_none: bool = False) -> float | None:
    moment = _number(value, allow_none=allow_none)
    if moment is not None and not _EARLIEST <= moment <= now + _FUTURE_SLACK:
        raise ValueError('outside any plausible time')
    return moment


def _point(item, now: float, received: int) -> tuple:
    if not isinstance(item, dict):
        raise ValueError('not an object')
    point_id = item.get('id')
    if not isinstance(point_id, str) or not _ID.match(point_id):
        raise ValueError('id is missing or malformed')
    kind = item.get('kind')
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {", ".join(KINDS)}')
    fix = _time(item.get('ts'), now)
    lat = _number(item.get('latitude'))
    lon = _number(item.get('longitude'))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('coordinates out of range')
    accuracy = _number(item.get('accuracy'), allow_none=True)
    # CoreLocation reports an invalid reading as a negative accuracy; keeping
    # it would put a point on the map that the phone itself didn't believe.
    if accuracy is not None and accuracy < 0:
        raise ValueError('accuracy is negative (an invalid fix)')
    speed = _number(item.get('speed'), allow_none=True)
    departed = _time(item.get('departed'), now, allow_none=True) if kind == 'visit' else None
    if departed is not None and departed < fix:
        raise ValueError('departs before it arrives')
    return (point_id, fix, day_key_for(int(fix)), lat, lon, accuracy,
            _number(item.get('altitude'), allow_none=True),
            speed if speed is None or speed >= 0 else None,
            kind, departed, received)


def ingest(body) -> dict:
    """Store one batch. Returns how many were stored, plus the first rejections."""
    if not isinstance(body, dict):
        raise BatchError('body must be a JSON object')
    points = body.get('points', [])
    if not isinstance(points, list):
        raise BatchError('points must be a list')
    if len(points) > MAX_POINTS:
        raise BatchError(f'points holds more than {MAX_POINTS} items')

    now = time.time()
    received = int(now)
    rows, rejected, rejected_count = [], [], 0
    for i, item in enumerate(points):
        try:
            rows.append(_point(item, now, received))
        except ValueError as e:
            rejected_count += 1
            if len(rejected) < MAX_REPORTED_REJECTIONS:
                rejected.append({'index': i, 'reason': str(e)})

    db = get_db()
    db.executemany(
        'INSERT OR REPLACE INTO location_points (id, fix_ts, day_key, latitude, longitude, accuracy_m,'
        ' altitude_m, speed_mps, kind, departed_ts, received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        rows,
    )
    db.commit()
    return {'points': len(rows), 'rejectedCount': rejected_count, 'rejected': rejected}
