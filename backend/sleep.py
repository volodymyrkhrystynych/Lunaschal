"""When the user woke up and when they went to sleep, per day.

Both ends come out of one rule: a day runs 04:00 -> 04:00 local, so waking is
the first thing the user did inside that window and going to sleep is the last.
The window itself is not a new idea here -- `backend/day_boundary.py` already
anchors the app's day at 04:00, and this reuses its `day_key_for`/`day_bounds`
rather than growing a second copy of the 4.

Three things are load-bearing:

- **Derived on read, never stored.** There is no nightly job and no snapshot
  row. `created_at` is always "now", so once a day's window has passed nothing
  new can land inside it and a derived time for a past day cannot drift. That
  removes the whole class of bug where a summary row and the data it summarises
  disagree.
- **Only manual values are persisted.** A `sleep_logs` row exists only for a day
  the user corrected by hand, and its two columns are independently nullable, so
  "the wake time is right but I put the phone down and read for an hour" is one
  manual end and one derived end rather than a day the user has to type twice.
- **A derived sleep time is withheld while the day is still being lived.** The
  last thing the user did today was five minutes ago and is not a bedtime;
  publishing it would tell the day view to draw the evening as already asleep. A
  *manual* sleep time is shown immediately -- the user saying it is the one thing
  that isn't a guess.
"""
import time

from ulid import ULID

from backend.day_boundary import DAY_ROLLOVER_HOUR, day_bounds
from backend.db.connection import get_db

# Tables whose rows mean "the user was awake and doing something", with the
# filter that keeps a row honest. Table names are fixed here and never come from
# a request, which is what makes interpolating them into the query safe.
#
# Assistant and system messages are excluded: a reply is the app being awake,
# not the user. Nudges, the morning check-in and Writing/Ideas discussions all
# post to /api/chat/stream, but only calls carrying a conversationId persist a
# row -- so background chatter can't fake activity, while a discussion the user
# actually typed in counts, as it should.
SIGNALS = (
    ('journal_entries', ''),
    ('messages', "AND role = 'user'"),
    ('transcriptions', ''),
    ('food_entries', ''),
    ('calorie_logs', ''),
)


def derive_window(day_key: str) -> tuple[int | None, int | None]:
    """(first, last) activity timestamps inside a day key's window.

    (None, None) for a day the user never touched the app -- which stays None
    rather than falling back to the window's own bounds, because "no data" and
    "asleep all day" are different claims and only one of them is ours to make.
    """
    start, end = day_bounds(day_key)
    firsts: list[int] = []
    lasts: list[int] = []
    db = get_db()
    for table, extra in SIGNALS:
        row = db.execute(
            f'SELECT MIN(created_at) AS first, MAX(created_at) AS last FROM {table}'
            f' WHERE created_at >= ? AND created_at < ? {extra}',
            (start, end),
        ).fetchone()
        if row and row['first'] is not None:
            firsts.append(row['first'])
            lasts.append(row['last'])
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


def _manual(day_key: str) -> tuple[int | None, int | None]:
    row = get_db().execute(
        'SELECT wake_at, sleep_at FROM sleep_logs WHERE date = ?', (day_key,)
    ).fetchone()
    if row is None:
        return (None, None)
    return (row['wake_at'], row['sleep_at'])


def resolve_day(day_key: str, now: int | None = None) -> dict:
    """The day's wake/sleep as the UI should show them, manual overriding auto.

    `wakeSource`/`sleepSource` are 'manual', 'auto', or None when that end isn't
    known -- the UI needs to tell a value it may correct from one it wrote.
    """
    now = int(time.time()) if now is None else now
    manual_wake, manual_sleep = _manual(day_key)
    auto_wake, auto_sleep = derive_window(day_key)

    # Still inside the window: today's last action isn't a bedtime yet.
    if now < day_bounds(day_key)[1]:
        auto_sleep = None

    wake = manual_wake if manual_wake is not None else auto_wake
    sleep = manual_sleep if manual_sleep is not None else auto_sleep
    return {
        'date': day_key,
        # Unix seconds, deliberately: the day view places these against a
        # date's own midnight, and an instant is the only form of that which
        # survives a value landing on the *next* calendar date (a 01:30
        # bedtime). row_to_dict's ISO conversion is UTC-based and would lose
        # the local wall clock this is entirely about.
        'wakeAt': wake,
        'sleepAt': sleep,
        'wakeSource': 'manual' if manual_wake is not None else ('auto' if wake is not None else None),
        'sleepSource': 'manual' if manual_sleep is not None else ('auto' if sleep is not None else None),
    }


def time_to_timestamp(day_key: str, hhmm: str) -> int:
    """'HH:MM' -> the unix second it names *inside* this day key's window.

    A time before the 04:00 rollover therefore lands on the following calendar
    date: someone who says they went to sleep at 01:30 on Tuesday means the
    small hours of Wednesday, and storing it against Tuesday's midnight would
    put bedtime 23 hours before it happened.
    """
    hour, minute = int(hhmm[:2]), int(hhmm[3:5])
    start, _ = day_bounds(day_key)
    offset_hours = hour - DAY_ROLLOVER_HOUR
    if offset_hours < 0:
        offset_hours += 24
    return start + offset_hours * 3600 + minute * 60


def set_day(day_key: str, *, wake: int | None, sleep: int | None) -> dict:
    """Store manual ends for a day. A None column falls back to the derived
    value, so clearing one end is setting it to None, not deleting the row."""
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO sleep_logs(id, date, wake_at, sleep_at, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?)'
        ' ON CONFLICT(date) DO UPDATE SET wake_at=excluded.wake_at,'
        ' sleep_at=excluded.sleep_at, updated_at=excluded.updated_at',
        (str(ULID()), day_key, wake, sleep, now, now),
    )
    db.commit()
    return resolve_day(day_key)


def clear_day(day_key: str) -> dict:
    """Drop the manual row, handing the day back to the derived values."""
    db = get_db()
    db.execute('DELETE FROM sleep_logs WHERE date = ?', (day_key,))
    db.commit()
    return resolve_day(day_key)
