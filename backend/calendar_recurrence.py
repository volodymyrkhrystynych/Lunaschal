"""Pure date expansion for repeating calendar events — no DB, no Flask.

Calendar events are wall-clock strings ('YYYY-MM-DD' + 'HH:MM'), never unix
timestamps, so everything here works on `date` objects and ISO strings. That
deliberately keeps recurrence out of the timezone question: "work on Monday"
means the user's Monday regardless of where the process thinks UTC is.

Weekdays are numbered Sunday=0 … Saturday=6 to match JS `Date.getDay()` and the
Sunday-first grid the UI renders, *not* Python's Monday=0.
"""

import calendar
from datetime import date, timedelta

VALID_FREQS = ('daily', 'weekly', 'monthly', 'yearly')

# Ceiling on occurrences produced for a single series in one call. A range query
# never legitimately needs this many (a daily event over a year is 366), so it
# only ever trips on a nonsense rule — and keeps that from hanging a request.
MAX_OCCURRENCES = 400


def parse_byweekday(csv: str | None) -> set[int]:
    """'1,2,3' -> {1, 2, 3}. Tolerant: NULL, blanks and garbage yield an empty
    set, which callers treat as "fall back to the anchor's own weekday"."""
    if not csv:
        return set()
    days = set()
    for part in str(csv).split(','):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.add(int(part))
    return days


def format_byweekday(days) -> str | None:
    """{5, 1, 3} -> '1,3,5'. Empty/None -> None (stored as SQL NULL)."""
    if not days:
        return None
    valid = sorted({int(d) for d in days if isinstance(d, int) or str(d).isdigit()})
    valid = [d for d in valid if 0 <= d <= 6]
    return ','.join(str(d) for d in valid) or None


def _js_weekday(d: date) -> int:
    """Python's Monday=0 -> JS's Sunday=0."""
    return (d.weekday() + 1) % 7


def _week_start(d: date) -> date:
    """The Sunday on or before `d`."""
    return d - timedelta(days=_js_weekday(d))


def _add_months(d: date, months: int) -> date:
    """Same day-of-month `months` later, clamped to the target month's length
    (Jan 31 + 1 month -> Feb 28/29), matching todo_recurrence.add_interval."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _add_years(d: date, years: int) -> date:
    """Same month/day `years` later, clamped to the target month's length
    (Feb 29 + 1 year -> Feb 28), matching _add_months.

    Clamping rather than skipping is the deliberate choice: a Feb 29 birthday
    should show up every year, and the rest of this module already resolves the
    same "that day doesn't exist here" problem the same way.
    """
    year = d.year + years
    return date(year, d.month, min(d.day, calendar.monthrange(year, d.month)[1]))


def occurrence_dates(event: dict, start: str, end: str) -> list[str]:
    """ISO dates on which `event` occurs within [start, end] inclusive.

    A non-repeating event yields its own date if it falls in the window. The
    event's `date` is the anchor: occurrences never precede it, and stop after
    `repeat_until`.
    """
    anchor = _as_date(event.get('date'))
    if anchor is None:
        return []
    win_start, win_end = _as_date(start), _as_date(end)
    if win_start is None or win_end is None or win_end < win_start:
        return []

    freq = event.get('repeat_freq')
    if freq not in VALID_FREQS:
        return [anchor.isoformat()] if win_start <= anchor <= win_end else []

    until = _as_date(event.get('repeat_until'))
    if until is not None and until < win_end:
        win_end = until
    if win_end < anchor or win_end < win_start:
        return []

    interval = event.get('repeat_interval') or 1
    try:
        interval = max(1, int(interval))
    except (TypeError, ValueError):
        interval = 1

    if freq == 'daily':
        dates = _daily(anchor, win_start, win_end, interval)
    elif freq == 'weekly':
        dates = _weekly(event, anchor, win_start, win_end, interval)
    elif freq == 'yearly':
        dates = _yearly(anchor, win_start, win_end, interval)
    else:
        dates = _monthly(anchor, win_start, win_end, interval)
    return [d.isoformat() for d in dates[:MAX_OCCURRENCES]]


def _daily(anchor: date, win_start: date, win_end: date, interval: int) -> list[date]:
    # Jump straight to the first on-cycle day at or after the window start
    # rather than walking day by day from the anchor.
    behind = max(0, (win_start - anchor).days)
    steps = -(-behind // interval)  # ceil
    current = anchor + timedelta(days=steps * interval)
    out = []
    while current <= win_end and len(out) < MAX_OCCURRENCES:
        out.append(current)
        current += timedelta(days=interval)
    return out


def _weekly(event: dict, anchor: date, win_start: date,
            win_end: date, interval: int) -> list[date]:
    weekdays = parse_byweekday(event.get('repeat_byweekday')) or {_js_weekday(anchor)}
    anchor_week = _week_start(anchor)
    # First on-cycle week at or after the window start.
    weeks_behind = max(0, (_week_start(win_start) - anchor_week).days // 7)
    steps = -(-weeks_behind // interval)  # ceil
    week = anchor_week + timedelta(weeks=steps * interval)
    out = []
    while week <= win_end and len(out) < MAX_OCCURRENCES:
        for offset in sorted(weekdays):
            day = week + timedelta(days=offset)
            if anchor <= day <= win_end and day >= win_start:
                out.append(day)
        week += timedelta(weeks=interval)
    return out


def _monthly(anchor: date, win_start: date, win_end: date, interval: int) -> list[date]:
    months_behind = max(
        0, (win_start.year - anchor.year) * 12 + (win_start.month - anchor.month)
    )
    steps = -(-months_behind // interval)  # ceil
    out = []
    while len(out) < MAX_OCCURRENCES:
        current = _add_months(anchor, steps * interval)
        if current > win_end:
            break
        if current >= win_start and current >= anchor:
            out.append(current)
        steps += 1
    return out


def _yearly(anchor: date, win_start: date, win_end: date, interval: int) -> list[date]:
    years_behind = max(0, win_start.year - anchor.year)
    steps = -(-years_behind // interval)  # ceil
    out = []
    while len(out) < MAX_OCCURRENCES:
        current = _add_years(anchor, steps * interval)
        if current > win_end:
            break
        if current >= win_start and current >= anchor:
            out.append(current)
        steps += 1
    return out


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def expand_events(events: list[dict], exceptions: list[dict],
                  start: str, end: str) -> list[dict]:
    """Turn stored event rows into concrete instances within [start, end].

    Each instance keeps the series `id` (so the details modal and every
    /api/calendar/<id> route keep working) and gains `occurrenceDate` plus
    `isRecurring`. `skip` exceptions drop an occurrence; `move` exceptions
    rewrite its date/times — and a moved occurrence can land inside the window
    even when its original date sits outside it.
    """
    win_start, win_end = _as_date(start), _as_date(end)
    if win_start is None or win_end is None or win_end < win_start:
        return []

    by_event: dict[str, dict[str, dict]] = {}
    for exc in exceptions:
        by_event.setdefault(exc['event_id'], {})[exc['date']] = exc

    out: list[dict] = []
    for event in events:
        excs = by_event.get(event.get('id'), {})
        # Moves can pull an occurrence in from outside the window, so widen the
        # search by the largest move distance before filtering on the result.
        for iso in occurrence_dates(event, *_search_window(excs, start, end)):
            exc = excs.get(iso)
            if exc and exc['action'] == 'skip':
                continue
            instance = dict(event)
            instance['occurrenceDate'] = iso
            instance['isRecurring'] = event.get('repeat_freq') in VALID_FREQS
            instance['date'] = iso
            if exc and exc['action'] == 'move':
                if exc.get('new_date'):
                    instance['date'] = exc['new_date']
                if exc.get('new_time'):
                    instance['time'] = exc['new_time']
                if exc.get('new_end_time'):
                    instance['end_time'] = exc['new_end_time']
            landed = _as_date(instance['date'])
            if landed is None or landed < win_start or landed > win_end:
                continue
            out.append(instance)
    out.sort(key=lambda e: (e['date'], e.get('time') or ''))
    return out


def _search_window(excs: dict[str, dict], start: str, end: str) -> tuple[str, str]:
    """Widen [start, end] enough to catch occurrences whose `move` exception
    relocates them into the window from outside it."""
    if not excs:
        return start, end
    win_start, win_end = _as_date(start), _as_date(end)
    lo, hi = win_start, win_end
    for iso, exc in excs.items():
        if exc['action'] != 'move' or not exc.get('new_date'):
            continue
        origin, moved = _as_date(iso), _as_date(exc['new_date'])
        if origin is None or moved is None:
            continue
        if win_start <= moved <= win_end:
            lo, hi = min(lo, origin), max(hi, origin)
    return lo.isoformat(), hi.isoformat()
