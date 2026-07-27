"""The single read path for calendar events.

Every consumer — the API routes, the morning briefing, the chat system prompt —
must go through `events_in_range`. A raw `SELECT ... WHERE date BETWEEN ?` only
sees a recurring series on its anchor date, so any reader that skips this
helper silently loses the user's standing commitments.
"""

from backend.calendar_recurrence import expand_events


def events_in_range(db, start: str, end: str) -> list[dict]:
    """One-off events in [start, end] plus expanded recurring occurrences.

    Returns raw snake_case row dicts (callers pass them through `row_to_dict`
    themselves when they need camelCase), sorted by date then time.
    """
    if not start or not end:
        return []

    singles = db.execute(
        '''SELECT * FROM calendar_events
           WHERE repeat_freq IS NULL AND date BETWEEN ? AND ?''',
        (start, end),
    ).fetchall()

    # Recurring series are fetched wholesale: whether a series touches the
    # window depends on its rule, not on its anchor date, so it can't be
    # narrowed in SQL. `repeat_until` is the one bound that can be.
    series = db.execute(
        '''SELECT * FROM calendar_events
           WHERE repeat_freq IS NOT NULL
             AND date <= ?
             AND (repeat_until IS NULL OR repeat_until >= ?)''',
        (end, start),
    ).fetchall()

    events = [dict(r) for r in singles] + [dict(r) for r in series]
    exceptions: list[dict] = []
    if series:
        ph = ','.join('?' * len(series))
        exceptions = [
            dict(r) for r in db.execute(
                f'SELECT * FROM calendar_event_exceptions WHERE event_id IN ({ph})',
                [r['id'] for r in series],
            ).fetchall()
        ]

    return expand_events(events, exceptions, start, end)
