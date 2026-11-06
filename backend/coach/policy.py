"""When a reminder may go out at all. Pure.

The bandit decides *how* and *at which of these times*; this decides whether
any reminder is allowed: inside quiet hours, past the day's cap, for
something already done, or for a habit that has stuck — no. Each rule is
checked when the reminder is due, not when the day is planned, because by
then the weigh-in may have happened and the evening may have become quiet.
"""
from datetime import date, datetime, time as dtime, timedelta

# Times a behaviour's reminder may be planned for. Weigh-ins start at 07:30
# so a smart scale's reading has a chance to reach the server through the
# phone's Health sync first.
SLOTS = {
    'weight': ['07:30', '09:00', '11:00'],
    'calories': ['13:00', '19:30', '21:00'],
    'habit': ['08:00', '12:30', '18:00', '20:30'],
    'goals': ['09:00', '12:00', '19:00'],
}
# A habit done this many days running without a reminder has stuck: stop
# prompting it, except for one exploratory reminder in this many.
FADE_AFTER = 5
FADE_EXPLORE = 7
# How fast the reminder load decays: it halves each day.
BURDEN_DECAY = 0.5


def family(behavior: str) -> str:
    return behavior.split(':', 1)[0]


def slots(behavior: str) -> list[str]:
    return SLOTS[family(behavior)]


def slot_time(day: str, slot: str) -> int:
    """The moment a slot on a 4am day happens. A slot before 04:00 belongs to
    the early hours *after* the date, which is still that day."""
    hour, minute = (int(x) for x in slot.split(':'))
    moment = datetime.combine(date.fromisoformat(day), dtime(hour, minute))
    if hour < 4:
        moment += timedelta(days=1)
    return int(moment.timestamp())


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Quiet from `start` to `end` o'clock; the window may wrap midnight."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def burden(sent_times: list[int], now: int) -> float:
    """Recent reminders, each counting less the older it is."""
    return sum(BURDEN_DECAY ** ((now - t) / 86400) for t in sent_times if t <= now)


def blocked(*, behavior: str, hour: int, quiet: tuple[int, int], sent_today: int, cap: int,
            done: bool, unprompted_run: int, rng) -> str | None:
    """Why this reminder can't go out now, as the status to record — or None."""
    if done:
        return 'skipped_logged'
    if in_quiet_hours(hour, *quiet):
        return 'skipped_quiet'
    if sent_today >= cap:
        return 'skipped_cap'
    if family(behavior) == 'habit' and unprompted_run >= FADE_AFTER and rng.random() >= 1 / FADE_EXPLORE:
        return 'skipped_faded'
    return None
