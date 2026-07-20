"""Pure date arithmetic for repeating todos — no DB, no Flask."""

import calendar
from datetime import datetime, timezone

VALID_LISTS = ('todo', 'chores', 'archive')
VALID_UNITS = ('day', 'week', 'month')


def add_interval(ts: int, interval: int, unit: str) -> int:
    """Advance a unix timestamp by `interval` days/weeks/months.

    Month arithmetic clamps the day-of-month (Jan 31 + 1 month -> Feb 28/29),
    which loses the day-31 anchor for subsequent hops — acceptable for todos.
    """
    if unit == 'day':
        return ts + interval * 86400
    if unit == 'week':
        return ts + interval * 7 * 86400
    if unit == 'month':
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        total = dt.year * 12 + (dt.month - 1) + interval
        year, month = divmod(total, 12)
        month += 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return int(dt.replace(year=year, month=month, day=day).timestamp())
    raise ValueError(f'unknown repeat unit: {unit}')


def next_due(due: int | None, interval: int, unit: str, now: int) -> int:
    """Next due date after completing a repeating todo.

    Anchors on the existing due date (so a monthly todo stays on the 15th)
    but rolls forward until strictly after `now`, so completing a
    long-overdue todo never produces another past due date.
    """
    candidate = add_interval(due if due is not None else now, interval, unit)
    while candidate <= now:
        candidate = add_interval(candidate, interval, unit)
    return candidate
