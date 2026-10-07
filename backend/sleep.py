"""When the user woke up and when they went to sleep, per day.

Both ends come out of one rule: a day runs 04:00 -> 04:00 local, so waking is
the first thing the user did inside that window and going to sleep is the last.
The window itself is not a new idea here -- `backend/day_boundary.py` already
anchors the app's day at 04:00, and this reuses its `day_key_for`/`day_bounds`
rather than growing a second copy of the 4.

Four things are load-bearing:

- **Derived on read, never stored.** There is no nightly job and no snapshot
  row, which removes the whole class of bug where a summary row and the data it
  summarises disagree. A past day can still change, and that is deliberate: the
  phone works offline and stamps what it sends with the moment it happened, so
  an entry written at 01:00 and synced at 09:00 moves that night's bedtime when
  it arrives. Only a hand-set end is immune, which is what setting it means.
- **Apple Health sits between the two.** When the Watch recorded the night,
  its sleep start/end replaces the activity guess for that end
  (backend/apple_health/nights.py); a hand correction still wins over it. Health
  samples are stored rows, so this stays derived on read like the rest.
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
from datetime import date, timedelta

from ulid import ULID

from backend.day_boundary import DAY_ROLLOVER_HOUR, day_bounds
from backend.db.connection import get_db
from backend.apple_health import nights

# Where the user being awake is written down: (table, timestamp columns,
# filter). Every column holds moments the user was awake. Table and column names are fixed here and never come from a
# request, which is what makes interpolating them into the query safe.
#
# Assistant and system messages are excluded: a reply is the app being awake,
# not the user. Nudges, the morning check-in and Writing/Ideas discussions all
# post to /api/chat/stream, but only calls carrying a conversationId persist a
# row -- so background chatter can't fake activity, while a discussion the user
# actually typed in counts, as it should.
#
# A reading span is a range, so both of its ends are moments: a chapter opened
# at 23:50 and put down at 00:40 went to sleep at 00:40, and each end counts
# for whichever day's window it falls in. task_events is the to-do
# log (ticked, completed, removed) and every row of it is a tap by the user.
#
# The last three are *last touched* columns, not logs: each holds only the most
# recent time, so working on a paper again on Wednesday takes Monday's late
# session out of Monday's evidence. That is the price of counting them at all
# -- nothing else records drawing, studying or reading the paper -- and it can
# only ever remove an old moment, never invent one, so a past day can lose its
# latest activity but never gain a false one.
#
# What the phone sends is stamped with the time it happened on the device, not
# when it synced -- a background sync at 03:00 is the phone, not the user. Its
# journal entries, food, calorie logs, voice messages, to-do ticks and reading
# spans all carry it. A drawing published from the iPad does not yet: it lands
# on `papers.content_updated_at` at the moment it reaches the server.
SIGNALS = (
    ('journal_entries', ('created_at',), ''),
    ('messages', ('created_at',), "AND role = 'user'"),
    ('transcriptions', ('created_at',), ''),
    ('food_entries', ('created_at',), ''),
    ('calorie_logs', ('created_at',), ''),
    ('fic_reading_spans', ('started_at', 'ended_at'), ''),
    ('task_events', ('created_at',), ''),
    ('papers', ('content_updated_at',), ''),
    ('study_sources', ('last_opened_at',), ''),
    ('newspaper_issues', ('last_read_at',), ''),
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
    for table, columns, extra in SIGNALS:
        for column in columns:
            row = db.execute(
                f'SELECT MIN({column}) AS first, MAX({column}) AS last FROM {table}'
                f' WHERE {column} >= ? AND {column} < ? {extra}',
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


def _health(day_key: str) -> tuple[int | None, int | None]:
    """Wake and bedtime from Apple Health: the end of the night that began this
    day, and the start of the one that ended it (backend/apple_health/nights.py)."""
    this_night = nights.night_for_day(day_key)
    next_night = nights.night_for_day(_next_day(day_key))
    wake = this_night[1] if this_night else None
    sleep = next_night[0] if next_night else None
    # Two nights picked independently can, on a fragmented weekend, disagree
    # about which is which; a bedtime before the wake is not a day.
    if wake is not None and sleep is not None and sleep <= wake:
        sleep = None
    return wake, sleep


def _next_day(day_key: str) -> str:
    return (date.fromisoformat(day_key) + timedelta(days=1)).isoformat()


def _pick(manual: int | None, health: int | None, auto: int | None) -> tuple[int | None, str | None]:
    for value, source in ((manual, 'manual'), (health, 'health'), (auto, 'auto')):
        if value is not None:
            return value, source
    return None, None


def resolve_day(day_key: str, now: int | None = None) -> dict:
    """The day's wake/sleep as the UI should show them: manual over Apple
    Health over the activity-derived guess, each end decided on its own.

    `wakeSource`/`sleepSource` are 'manual', 'health', 'auto', or None when that
    end isn't known -- the UI needs to tell a value it may correct from one it
    wrote, and a measurement from a guess.
    """
    now = int(time.time()) if now is None else now
    manual_wake, manual_sleep = _manual(day_key)
    health_wake, health_sleep = _health(day_key)
    auto_wake, auto_sleep = derive_window(day_key)

    # Still inside the window: today's last action isn't a bedtime yet. A
    # health bedtime needs no such guard -- it is a night that already began.
    if now < day_bounds(day_key)[1]:
        auto_sleep = None

    wake, wake_source = _pick(manual_wake, health_wake, auto_wake)
    sleep, sleep_source = _pick(manual_sleep, health_sleep, auto_sleep)
    return {
        'date': day_key,
        # Unix seconds, deliberately: the day view places these against a
        # date's own midnight, and an instant is the only form of that which
        # survives a value landing on the *next* calendar date (a 01:30
        # bedtime). row_to_dict's ISO conversion is UTC-based and would lose
        # the local wall clock this is entirely about.
        'wakeAt': wake,
        'sleepAt': sleep,
        'wakeSource': wake_source,
        'sleepSource': sleep_source,
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
