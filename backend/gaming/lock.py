"""The gaming lock: on a workday, games stay closed until work is closed out.

Whether today is locked is **derived, never stored**: the lock is on, today's
4am day falls on a lock weekday, and no `gaming_unlocks` row exists for it. So
a new day locks itself at 4am with no job to run, and a server that was off
all night is right on its first tick — the same reasoning as the Journal's
filing flag.

There are two ways out, and the record says which. Telling the chat agent
you're done with work (`end_workday` → the `unlock_gaming` card) unlocks with
the day's provisional rating stamped on the row. The override unlocks without
the review, takes a typed reason, and counts as a missed item in that day's
review (`backend/coach/day_facts.py`). Nothing stops the override — a lock
with no way out is a lock that gets switched off for good — it just costs
something you can see the next morning.
"""
import json
import time
from datetime import date

from ulid import ULID

from backend.day_boundary import day_key_for

DEFAULT_DAYS = [0, 1, 2, 3, 4]  # Monday–Friday, date.weekday()
# Matched against a process's name and its first argument's basename,
# case-insensitively. Wine processes show as the Windows exe name.
DEFAULT_PATTERNS = ['steam', 'steam.sh', 'steamwebhelper', 'Battle.net.exe', 'Battle.net Launcher.exe',
                    'Agent.exe', 'lutris', 'heroic', 'legendary']
METHODS = ('review', 'override')
MIN_REASON = 10


def _now(now):
    return int(now if now is not None else time.time())


def _json_list(raw, default):
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        value = None
    return value if isinstance(value, list) else list(default)


def settings(db) -> dict:
    row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
    row = dict(row) if row else {}
    days = [d for d in _json_list(row.get('gaming_lock_days'), DEFAULT_DAYS)
            if isinstance(d, int) and 0 <= d <= 6]
    patterns = [p for p in _json_list(row.get('gaming_lock_patterns'), DEFAULT_PATTERNS)
                if isinstance(p, str) and p.strip()]
    return {'enabled': bool(row.get('gaming_lock_enabled')), 'days': sorted(set(days)),
            'patterns': patterns}


def update_settings(db, changes: dict) -> dict:
    for key, value in changes.items():
        if key == 'enabled':
            if not isinstance(value, bool):
                raise ValueError('enabled must be true or false')
            db.execute('UPDATE settings SET gaming_lock_enabled=? WHERE id=1', (int(value),))
        elif key == 'days':
            if (not isinstance(value, list)
                    or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in value)):
                raise ValueError('days must be a list of weekdays 0 (Monday) to 6 (Sunday)')
            db.execute('UPDATE settings SET gaming_lock_days=? WHERE id=1', (json.dumps(sorted(set(value))),))
        elif key == 'patterns':
            if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                raise ValueError('patterns must be a list of process names')
            cleaned = [p.strip() for p in value if p.strip()][:50]
            if any(len(p) > 100 for p in cleaned):
                raise ValueError('a process name is too long')
            db.execute('UPDATE settings SET gaming_lock_patterns=? WHERE id=1', (json.dumps(cleaned),))
        else:
            raise ValueError(f'Unknown gaming lock setting {key!r}')
    db.commit()
    return settings(db)


def unlock_row(db, day: str) -> dict | None:
    row = db.execute('SELECT * FROM gaming_unlocks WHERE day=?', (day,)).fetchone()
    return dict(row) if row else None


def applies(db, day: str) -> bool:
    """Whether `day` is one the lock covers at all."""
    s = settings(db)
    return s['enabled'] and date.fromisoformat(day).weekday() in s['days']


def locked(db, now: int | None = None) -> bool:
    day = day_key_for(_now(now))
    return applies(db, day) and unlock_row(db, day) is None


def work_end(db, day: str) -> str | None:
    """When the calendar says today's work ends ("HH:MM"), if it says.
    The location trail already moves the work occurrence to the real
    departure (backend/location/work_calendar.py), so this is the honest
    end whenever the phone was there."""
    from backend.calendar_query import events_in_range
    from backend.location.work_calendar import is_work_event
    ends = [e['end_time'] for e in events_in_range(db, day, day)
            if e.get('date') == day and e.get('end_time') and not e.get('all_day') and is_work_event(e)]
    return max(ends) if ends else None


def status(db, now: int | None = None) -> dict:
    now = _now(now)
    day = day_key_for(now)
    row = unlock_row(db, day)
    return {
        'day': day,
        'applies': applies(db, day),
        'locked': applies(db, day) and row is None,
        'unlockedAt': row['unlocked_at'] if row else None,
        'method': row['method'] if row else None,
        'reason': row['reason'] if row else None,
        'ratingAtUnlock': row['rating_at_unlock'] if row else None,
        'workEnd': work_end(db, day),
        'settings': settings(db),
    }


def unlock(db, day: str, method: str, *, reason: str | None = None, rating: int | None = None,
           now: int | None = None) -> dict:
    """Unlock `day`. Idempotent: a day already unlocked keeps its first row,
    so a review unlock after an override does not launder the override."""
    if method not in METHODS:
        raise ValueError(f'method must be one of {", ".join(METHODS)}')
    reason = (reason or '').strip() or None
    if method == 'override' and (reason is None or len(reason) < MIN_REASON):
        raise ValueError(f'Say why, in at least {MIN_REASON} characters')
    if reason and len(reason) > 500:
        raise ValueError('reason must be at most 500 characters')
    # OR IGNORE rather than check-then-insert: the card and the override can
    # land together (phone and web), and UNIQUE(day) keeps whichever came first.
    db.execute('INSERT OR IGNORE INTO gaming_unlocks'
               ' (id, day, method, reason, rating_at_unlock, work_end, unlocked_at)'
               ' VALUES (?,?,?,?,?,?,?)',
               (str(ULID()), day, method, reason, rating, work_end(db, day), _now(now)))
    db.commit()
    return unlock_row(db, day)


def lift(db, method: str, *, reason: str | None = None, now: int | None = None) -> dict:
    """Unlock today and bring today's review up to date with it.

    A review unlock stamps the rating the user was just shown; an override
    adds a missed item to the day (`day_facts.gather`), so the review is
    revised afterwards and any card already in the chat shows the new rating.
    """
    from backend.coach import review
    now = _now(now)
    day = day_key_for(now)
    if not applies(db, day):
        raise ValueError('Games are not locked today')
    rating = review.draft(day, now)['rating'] if method == 'review' else None
    row = unlock(db, day, method, reason=reason, rating=rating, now=now)
    if row['method'] == 'override':
        review.revise(day, now)
    return status(db, now)
