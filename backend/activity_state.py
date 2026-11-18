"""Work / leisure / mix: the state the user switches from the Watch or the
phone, and the spans a day is drawn and counted from.

A switch is an instant; the state it names lasts until the next one. Spans are
derived on read and never stored, so a switch that arrives late from the
Watch's outbox lands in the right place the next time anything looks. Work
that overlaps fic reading becomes mix for that stretch: reading on the clock is
not work, and the calendar's two rails would otherwise disagree.

The pure functions take plain tuples so they can be tested without a database;
`day_spans` is the one that reads it.
"""
import re
import time

from backend import sleep
from backend.day_boundary import day_bounds

STATES = ('work', 'leisure', 'mix')
SOURCES = ('watch', 'phone')

_ULID_RE = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}')

# A switch is stamped by the device that made it; a Watch whose clock has
# drifted a little is fine, one dated tomorrow is broken.
FUTURE_SLACK = 300

# The same floor the reading rail uses, so the two rails agree on what counts.
MIN_READING_SECONDS = 60


def parse_switch(body, now: int) -> dict:
    """The row to insert, or ValueError naming what is wrong."""
    if not isinstance(body, dict):
        raise ValueError('expected a JSON object')
    switch_id = body.get('id')
    if not isinstance(switch_id, str) or not _ULID_RE.fullmatch(switch_id.upper()):
        raise ValueError('id must be a ULID')
    state = body.get('state')
    if state not in STATES:
        raise ValueError(f'state must be one of {", ".join(STATES)}')
    at = body.get('at')
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        raise ValueError('at must be a number')
    if at > now + FUTURE_SLACK:
        raise ValueError('at is in the future')
    source = body.get('source', 'phone')
    if source not in SOURCES:
        raise ValueError(f'source must be one of {", ".join(SOURCES)}')
    return {'id': switch_id.upper(), 'state': state, 'at': int(at), 'source': source}


def spans(switches, start: int, end: int, now: int) -> list[dict]:
    """Each state's stretch inside [start, end), as {id, state, startedAt, endedAt}.

    `switches` is (id, state, at) in any order and may include ones before
    `start`: the last of those is what the day opens in. Time before the first
    switch anyone ever made is no state at all. The open last state runs to
    `now`, so today's rail stops at the present rather than at 4am tomorrow.
    """
    ordered = sorted(switches, key=lambda s: (s[2], s[0]))
    out: list[dict] = []
    for i, (switch_id, state, at) in enumerate(ordered):
        until = ordered[i + 1][2] if i + 1 < len(ordered) else now
        a, b = max(at, start), min(until, end)
        if b <= a:
            continue
        if out and out[-1]['state'] == state and out[-1]['endedAt'] == a:
            out[-1]['endedAt'] = b  # switched to the state it was already in
        else:
            out.append({'id': switch_id, 'state': state, 'startedAt': a, 'endedAt': b})
    return out


def with_reading(state_spans: list[dict], reading) -> list[dict]:
    """Work spans split around reading, the overlapping pieces turned to mix.

    `reading` is (startedAt, endedAt) intervals, overlapping or not. Leisure
    and mix are left as they are: reading during leisure is still leisure.
    """
    merged: list[list[int]] = []
    for a, b in sorted(reading):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    out: list[dict] = []

    def push(span, state, a, b):
        if b <= a:
            return
        if out and out[-1]['state'] == state and out[-1]['endedAt'] == a:
            out[-1]['endedAt'] = b
            return
        # A span cut in pieces keeps its switch's id plus where the piece
        # starts, so each piece has its own id and the same one every read.
        piece_id = span['id'] if a == span['startedAt'] else f"{span['id']}@{a}"
        out.append({'id': piece_id, 'state': state, 'startedAt': a, 'endedAt': b})

    for span in state_spans:
        a, b = span['startedAt'], span['endedAt']
        if span['state'] != 'work':
            push(span, span['state'], a, b)
            continue
        cursor = a
        for ra, rb in merged:
            if rb <= cursor or ra >= b:
                continue
            push(span, 'work', cursor, max(cursor, ra))
            push(span, 'mix', max(cursor, ra), min(b, rb))
            cursor = min(b, rb)
        push(span, 'work', cursor, b)
    return out


def clip(state_spans: list[dict], lo: int | None, hi: int | None) -> list[dict]:
    """Spans trimmed to the waking day, so leisure left on overnight is not
    eight hours of leisure. An unknown end leaves that side as it was."""
    out = []
    for span in state_spans:
        a = span['startedAt'] if lo is None else max(span['startedAt'], lo)
        b = span['endedAt'] if hi is None else min(span['endedAt'], hi)
        if b > a:
            out.append({**span, 'startedAt': a, 'endedAt': b})
    return out


def totals(state_spans: list[dict]) -> dict:
    """Seconds in each state, every state present."""
    out = dict.fromkeys(STATES, 0)
    for span in state_spans:
        out[span['state']] += span['endedAt'] - span['startedAt']
    return out


def day_spans(db, day_key: str, now: int | None = None) -> list[dict]:
    """A 4am day's spans, reading folded in and trimmed to wake and sleep.

    The one read every consumer goes through — the calendar rail, the coach's
    review and the briefing — so they cannot disagree about a day.
    """
    now = int(time.time()) if now is None else now
    start, end = day_bounds(day_key)
    switches = db.execute(
        'SELECT id, state, at FROM activity_states WHERE at<?'
        ' AND at>=COALESCE((SELECT MAX(at) FROM activity_states WHERE at<=?), 0)'
        ' ORDER BY at, id', (end, start)).fetchall()
    base = spans([(r['id'], r['state'], r['at']) for r in switches], start, end, now)
    if not base:
        return []
    reading = db.execute(
        'SELECT started_at, ended_at FROM fic_reading_spans'
        ' WHERE started_at<? AND ended_at>? AND active_seconds>=?',
        (end, start, MIN_READING_SECONDS)).fetchall()
    merged = with_reading(base, [(r['started_at'], r['ended_at']) for r in reading])
    awake = sleep.resolve_day(day_key, now)
    return clip(merged, awake['wakeAt'], awake['sleepAt'])


def day_minutes(db, day_key: str, now: int | None = None) -> dict | None:
    """Whole minutes per state for the coach and the briefing, or None for a
    day nothing was switched — so a prompt can leave the line out rather than
    report a day of zeroes it knows nothing about."""
    day = day_spans(db, day_key, now)
    if not day:
        return None
    return {state: round(seconds / 60) for state, seconds in totals(day).items()}


def current(db) -> dict | None:
    row = db.execute(
        'SELECT id, state, at, source FROM activity_states ORDER BY at DESC, id DESC LIMIT 1'
    ).fetchone()
    return dict(row) if row else None
