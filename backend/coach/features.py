"""The context recorded with each reminder decision.

Logged for every decision — sent, held or skipped — so a richer model can be
fitted later from the same rows. Today the bandit uses none of it beyond what
the behaviour and slot already say; with one person's few decisions a day, a
contextual model would need months to beat a per-slot one, and logging is
what makes that possible later rather than starting from nothing.
"""
import time
from datetime import date, datetime, timedelta


def place(db, now: int) -> str:
    """home / work / away from the phone's last fix within 30 minutes, or
    unknown. Best effort: no trail, no saved places, it's unknown."""
    try:
        from backend.day_boundary import day_key_for
        from backend.location import lookup, trail
        from backend.location.whereabouts import match
        from backend.place_roles import roles_on
        fix = lookup.latest_fix(db, now - 1800)
        if fix is None:
            return 'unknown'
        roles = roles_on(db, day_key_for(now))
        places = ([roles['home']] if roles['home'] else []) + roles['work']
        found = match(trail.Fix(fix[2], fix[0], fix[1]), places)
        return found.role if found else 'away'
    except Exception:
        return 'unknown'


def context(db, *, behavior: str, day: str, now: int, sent_today: int, burden: float,
            done_days: list[str]) -> dict:
    moment = datetime.fromtimestamp(now)
    week = {(date.fromisoformat(day) - timedelta(days=i)).isoformat() for i in range(1, 8)}
    return {
        'hour': moment.hour,
        'weekend': moment.weekday() >= 5,
        'sentToday': sent_today,
        'burden': round(burden, 3),
        'doneLast7': round(len(week & set(done_days)) / 7, 3),
        'place': place(db, now),
    }
