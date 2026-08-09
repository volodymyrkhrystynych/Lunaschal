"""Pure date arithmetic and field validation for todos — no DB, no Flask.

The `parse_*` helpers live here rather than in backend/routes/tasks.py because
two callers need the identical rules: the todos API, and the chat's
`propose_task` tool (backend/delegate/tools.py). A second copy in the tool would
be a second set of bounds to drift out of sync, and the tool's whole job is to
stage something the API will later accept.
"""

import calendar
from datetime import datetime, timezone

VALID_LISTS = ('todo', 'chores', 'archive')
VALID_UNITS = ('day', 'week', 'month')


def parse_priority(value):
    """Returns (int_1_to_5, error_or_None). Absent/None -> default 3 (neutral)."""
    if value is None:
        return 3, None
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 5):
        return None, 'priority must be an integer from 1 to 5'
    return value, None


def parse_repeat(interval, unit):
    """Returns ((interval, unit) or (None, None), error_or_None)."""
    if interval is None and unit is None:
        return (None, None), None
    if interval is None or unit is None:
        return None, 'repeatInterval and repeatUnit must be set together'
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        return None, 'repeatInterval must be a positive integer'
    if unit not in VALID_UNITS:
        return None, f'repeatUnit must be one of {", ".join(VALID_UNITS)}'
    return (interval, unit), None


def parse_due_date(value):
    """'YYYY-MM-DD' -> (unix seconds at local noon, error_or_None).

    Noon, not midnight, matching src/lib/todos.ts's `dueInputToUnix`: the column
    round-trips through a UTC ISO string on the way back out, and midnight in a
    timezone east or west of UTC lands on the adjacent calendar day. The two
    encoders have to agree or a due date set in chat renders a day off from one
    set in the todo form.
    """
    if value is None or value == '':
        return None, None
    if not isinstance(value, str):
        return None, 'due must be a date as YYYY-MM-DD'
    try:
        d = datetime.strptime(value.strip(), '%Y-%m-%d')
    except ValueError:
        return None, 'due must be a real date as YYYY-MM-DD'
    return int(d.replace(hour=12).timestamp()), None


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
