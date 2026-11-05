"""Keeping the calendar's workday true to where the phone actually was.

When the trail shows the user at a Work place for a day, that day's occurrence
of their recurring work event is moved to the real arrival and departure — the
same `move` exception a drag in the day view writes. A day with no work event
scheduled gets a one-off "At <place>" event instead. A scheduled workday the
user never went to is left alone: an absence of trail is not proof of absence.

Three rules keep this from ever fighting the user:

- **It writes only what it can re-read.** `work_attendance` records the times
  it last wrote; if the stored occurrence no longer says that, the user edited
  it, and the day is *released* — never touched again.
- **It never takes over an occurrence the user already moved.** An exception
  that exists before this module wrote one is the user's.
- **Ongoing is not finished.** While the user is still at work the start is
  set and the planned end is kept; the departure lands when the phone reports
  the exit.

Runs on every upload that changed a day's stays (routes/location.py), so it is
synchronous, model-free and cheap — a handful of indexed reads per day.
"""
import json
import time
from datetime import datetime

from ulid import ULID

from backend.calendar_query import events_in_range
from backend.day_boundary import day_bounds
from backend.location import whereabouts
from backend.place_roles import list_periods, roles_for_day

# Less than this at work is a visit to the office, not a workday.
MIN_WORK_S = 30 * 60


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime('%H:%M')


def _tags(raw) -> list[str]:
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(t).strip().lower() for t in value] if isinstance(value, list) else []


def is_work_event(event: dict) -> bool:
    return 'work' in _tags(event.get('category_tags')) or 'work' in _tags(event.get('tags'))


def attendance(fixes, roles, day: str, now: float) -> dict | None:
    """When the user was at work on `day`, from the trail: first arrival, last
    departure (None while still there), the place they spent longest at."""
    works = [r for r in roles if r.role == 'work']
    if not works:
        return None
    spans = whereabouts.intervals(whereabouts.stays(fixes, works, now))
    if not spans:
        return None
    bounds = day_bounds(day)
    by_place: dict[str, float] = {}
    total = 0.0
    for span in spans:
        a, d = span.clipped(bounds, now)
        seconds = max(0.0, d - a)
        total += seconds
        by_place[span.place.name] = by_place.get(span.place.name, 0.0) + seconds
    if total < MIN_WORK_S:
        return None
    last = max(spans, key=lambda s: s.start)
    return {'arrived': max(spans[0].start, bounds[0]),
            'departed': None if last.end is None else min(last.end, bounds[1]),
            'place': max(by_place, key=by_place.get)}


def _still_ours(db, record) -> bool:
    written = (record['written_time'], record['written_end_time'])
    if record['occurrence_date']:
        row = db.execute('SELECT action, new_time, new_end_time FROM calendar_event_exceptions'
                         ' WHERE event_id=? AND date=?',
                         (record['event_id'], record['occurrence_date'])).fetchone()
        return row is not None and row['action'] == 'move' and (row['new_time'], row['new_end_time']) == written
    if record['created_event'] and record['event_id']:
        row = db.execute('SELECT time, end_time FROM calendar_events WHERE id=?', (record['event_id'],)).fetchone()
        return row is not None and (row['time'], row['end_time']) == written
    return False


def _candidate(db, day: str, arrived: float, departed: float | None):
    """The recurring work occurrence on `day` to rewrite, or None.
    'skip' means there is one but the user has already moved it."""
    occurrences = [e for e in events_in_range(db, day, day)
                   if e.get('isRecurring') and e.get('occurrenceDate') == day and e.get('time')
                   and not e.get('all_day') and is_work_event(e)]
    if not occurrences:
        return None
    end = departed if departed is not None else arrived + 3600

    def overlap(e) -> float:
        start = datetime.fromisoformat(f"{e['date']}T{e['time']}").timestamp()
        stop = (datetime.fromisoformat(f"{e['date']}T{e['end_time']}").timestamp()
                if e.get('end_time') else start + 3600)
        return max(0.0, min(stop, end) - max(start, arrived))

    best = max(occurrences, key=overlap)
    taken = db.execute('SELECT 1 FROM calendar_event_exceptions WHERE event_id=? AND date=?',
                       (best['id'], day)).fetchone()
    return 'skip' if taken else best


def sync_day(db, day: str, fixes, *, now: float | None = None) -> dict | None:
    """Bring `day`'s work event in line with the trail. Returns what it wrote,
    or None when it left the calendar alone."""
    now = time.time() if now is None else now
    record = db.execute('SELECT * FROM work_attendance WHERE day_key=?', (day,)).fetchone()
    if record is not None and record['released']:
        return None
    seen = attendance(fixes, roles_for_day(list_periods(db), day), day, now)
    if seen is None:
        return None
    start = _clock(seen['arrived'])
    end = _clock(seen['departed']) if seen['departed'] is not None else None
    stamp = int(now)

    if record is not None:
        if not _still_ours(db, record):
            db.execute('UPDATE work_attendance SET released=1, updated_at=? WHERE day_key=?', (stamp, day))
            db.commit()
            return None
        event_id, occurrence = record['event_id'], record['occurrence_date']
    else:
        found = _candidate(db, day, seen['arrived'], seen['departed'])
        if found == 'skip':
            return None
        event_id = found['id'] if found else None
        occurrence = day if found else None

    if occurrence:
        # A still-open day keeps the series' planned end; the exception's
        # NULL end means "unchanged" to the expansion.
        db.execute(
            '''INSERT INTO calendar_event_exceptions(id, event_id, date, action, new_date, new_time,
                                                     new_end_time, created_at)
               VALUES (?,?,?,'move',NULL,?,?,?)
               ON CONFLICT(event_id, date) DO UPDATE SET action='move', new_date=NULL,
                   new_time=excluded.new_time, new_end_time=excluded.new_end_time''',
            (str(ULID()), event_id, occurrence, start, end, stamp))
        created = 0
    else:
        date = datetime.fromtimestamp(seen['arrived']).date().isoformat()
        title = f"At {seen['place']}"
        if event_id:
            db.execute('UPDATE calendar_events SET title=?, date=?, time=?, end_time=? WHERE id=?',
                       (title, date, start, end, event_id))
        else:
            event_id = str(ULID())
            # Pre-classified as work, so the background classifier never
            # spends a model call on an event whose category is the point.
            db.execute(
                'INSERT INTO calendar_events (id, title, description, date, time, end_time, created_at,'
                ' category_tags, classified_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (event_id, title, 'Recorded from your location trail.', date, start, end, stamp,
                 json.dumps(['work']), stamp))
        created = 1
    db.execute(
        '''INSERT INTO work_attendance (day_key, event_id, occurrence_date, created_event, place_name,
                                        written_time, written_end_time, released, updated_at)
           VALUES (?,?,?,?,?,?,?,0,?)
           ON CONFLICT(day_key) DO UPDATE SET event_id=excluded.event_id,
               occurrence_date=excluded.occurrence_date, created_event=excluded.created_event,
               place_name=excluded.place_name, written_time=excluded.written_time,
               written_end_time=excluded.written_end_time, updated_at=excluded.updated_at''',
        (day, event_id, occurrence, created, seen['place'], start, end, stamp))
    db.commit()
    return {'eventId': event_id, 'occurrenceDate': occurrence, 'time': start, 'endTime': end,
            'created': bool(created), 'place': seen['place']}


def sync_days(db, day_keys) -> None:
    from backend.location import days
    for day in day_keys:
        sync_day(db, day, days.fixes(day))
