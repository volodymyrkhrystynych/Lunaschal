"""Weekly patterns in where the time goes: office days, hours at work, when the
day starts and ends there, and how long the commute takes. Pure — it reads the
per-day summaries backend/location/days.py already derives for the map card,
so a pattern can never disagree with the days it was built from.

Only days the phone recorded a trail count as observed. A week with three
trail days and two office days says "2 of 3", not "2 of 7": a day with no
trail is unknown, not a day at home.
"""
from datetime import date, datetime, timedelta

from backend.location.work_calendar import MIN_WORK_S

WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _minutes(iso: str | None) -> int | None:
    if not iso:
        return None
    local = datetime.fromisoformat(iso).astimezone()
    return local.hour * 60 + local.minute


def _clock(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    m = round(minutes)
    return f'{m // 60 % 24:02d}:{m % 60:02d}'


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def day_facts(day: str, summary: dict) -> dict:
    where = summary.get('whereabouts') or {}
    work = where.get('work') or []
    home = where.get('home') or {}
    seconds = sum(w['seconds'] for w in work)
    return {
        'day': day,
        'workSeconds': seconds,
        'homeSeconds': home.get('seconds', 0),
        'office': seconds >= MIN_WORK_S,
        'arrival': min((m for w in work if (m := _minutes(w['firstArrival'])) is not None), default=None),
        'departure': max((m for w in work if (m := _minutes(w['lastDeparture'])) is not None), default=None),
        'commutes': summary.get('commutes') or [],
    }


def _commute_stats(trips: list[dict]) -> dict:
    out = {}
    for direction in ('toWork', 'toHome'):
        mine = [t for t in trips if t['direction'] == direction]
        out[direction] = {'count': len(mine), 'avgSeconds': round(_mean([t['seconds'] for t in mine]) or 0) or None}
    modes: dict[str, int] = {}
    for t in trips:
        modes[t['mode']] = modes.get(t['mode'], 0) + 1
    out['modes'] = modes
    return out


def build(summaries: list[tuple[str, dict]]) -> dict:
    """`summaries` is [(day_key, {whereabouts, commutes}), ...] for observed days."""
    facts = [day_facts(day, s) for day, s in sorted(summaries)]
    weeks: dict[str, list[dict]] = {}
    for f in facts:
        d = date.fromisoformat(f['day'])
        weeks.setdefault((d - timedelta(days=d.weekday())).isoformat(), []).append(f)
    weekly = []
    for start, group in sorted(weeks.items(), reverse=True):
        office = [f for f in group if f['office']]
        weekly.append({
            'weekStart': start,
            'observedDays': len(group),
            'officeDays': len(office),
            'workSeconds': sum(f['workSeconds'] for f in group),
            'homeSeconds': sum(f['homeSeconds'] for f in group),
            'avgArrival': _clock(_mean([f['arrival'] for f in office])),
            'avgDeparture': _clock(_mean([f['departure'] for f in office])),
            'commute': _commute_stats([t for f in group for t in f['commutes']]),
        })
    by_weekday = []
    for i, name in enumerate(WEEKDAYS):
        mine = [f for f in facts if date.fromisoformat(f['day']).weekday() == i]
        by_weekday.append({'weekday': name, 'observedDays': len(mine),
                           'officeDays': sum(1 for f in mine if f['office'])})
    trips = [t for f in facts for t in f['commutes']]
    return {'weeks': weekly, 'weekdays': by_weekday, 'commute': _commute_stats(trips),
            'observedDays': len(facts), 'officeDays': sum(1 for f in facts if f['office'])}
