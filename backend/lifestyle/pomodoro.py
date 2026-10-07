"""Pomodoro runs from the Watch: validation of an uploaded run, and the per-day
summary the Lifestyle card draws.

A run arrives once from the phone's outbox under the id the Watch minted, so
the route inserts with OR IGNORE and a replay changes nothing. DB-free so the
rules can be tested on their own.
"""
import re
from datetime import date, timedelta

from backend.day_boundary import day_key_for

KINDS = ('work', 'break', 'timeout')

_ULID_RE = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}')

# The longest a run can be: the Watch's longest timer is 25 minutes, and a
# debug build shortens them. Anything past this is a broken clock.
MAX_SECONDS = 4 * 3600


def parse_session(body: dict, now: int) -> dict:
    """The row to insert, or ValueError naming what is wrong."""
    if not isinstance(body, dict):
        raise ValueError('expected a JSON object')
    session_id = body.get('id')
    if not isinstance(session_id, str) or not _ULID_RE.fullmatch(session_id.upper()):
        raise ValueError('id must be a ULID')
    kind = body.get('kind')
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {", ".join(KINDS)}')
    started, ended, planned = (
        _int(body.get(k), k) for k in ('startedAt', 'endedAt', 'plannedSeconds')
    )
    if ended < started:
        raise ValueError('endedAt is before startedAt')
    if ended - started > MAX_SECONDS or not 0 < planned <= MAX_SECONDS:
        raise ValueError('a pomodoro run cannot be that long')
    if started > now + 300:
        raise ValueError('startedAt is in the future')
    completed = body.get('completed')
    if not isinstance(completed, bool):
        raise ValueError('completed must be true or false')
    return {
        'id': str(session_id).upper(),
        'kind': kind,
        'date': day_key_for(started),
        'started_at': started,
        'ended_at': ended,
        'planned_seconds': planned,
        'completed': int(completed),
    }


def _int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number')
    return int(value)


def minutes(row) -> float:
    return (row['ended_at'] - row['started_at']) / 60


def summarize(rows, first: str, last: str) -> list[dict]:
    """One entry per day from first to last, empty days included, so the chart
    keeps a slot for a day with no runs. Minutes count what was actually spent,
    cancelled runs included; completedBlocks counts finished work runs only."""
    days = {}
    day = date.fromisoformat(first)
    end = date.fromisoformat(last)
    while day <= end:
        key = day.isoformat()
        days[key] = {'date': key, 'focusMinutes': 0.0, 'breakMinutes': 0.0,
                     'timeoutMinutes': 0.0, 'completedBlocks': 0}
        day += timedelta(days=1)
    field = {'work': 'focusMinutes', 'break': 'breakMinutes', 'timeout': 'timeoutMinutes'}
    for row in rows:
        entry = days.get(row['date'])
        if entry is None:
            continue
        entry[field[row['kind']]] += minutes(row)
        if row['kind'] == 'work' and row['completed']:
            entry['completedBlocks'] += 1
    for entry in days.values():
        for key in ('focusMinutes', 'breakMinutes', 'timeoutMinutes'):
            entry[key] = round(entry[key])
    return list(days.values())
