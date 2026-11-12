"""How long a day-plan suggestion the user keeps leaving undone stays out of
the plan. Pure and DB-free; backend/day_plan.py applies it.

The rule the user asked for: something skipped over and over should stop being
pushed, and after a while the plan should ask whether to keep it at all. One
skipped day is just a day — it costs nothing — so the gap only opens on the
second, and grows from there rather than dropping the item outright: a to-do
that matters will still come back, only less often.
"""
from datetime import date, timedelta

# Consecutive skipped days → days it sits out of the plan, counted from the
# day after the last skip. Past the table it stays at the last value.
GAPS = (0, 1, 3, 7)
# A habit is meant to be done daily; a long gap would let it vanish from the
# plan for a week because of one bad stretch.
DAILY_MAX_GAP = 1
# The streak at which the plan asks "keep it or let it go?".
ASK_AT_STREAK = 3
# A ✕ in the bar is a clearer "not now" than leaving it undone.
DISMISS_GAP = 3


def gap_days(streak: int, *, daily: bool = False) -> int:
    if streak <= 0:
        return 0
    gap = GAPS[min(streak, len(GAPS)) - 1]
    return min(gap, DAILY_MAX_GAP) if daily else gap


def eligible_from(last_skipped_day: str, streak: int, *, daily: bool = False) -> str | None:
    """The first day the item may be suggested again, or None when nothing
    holds it back."""
    gap = gap_days(streak, daily=daily)
    if not gap:
        return None
    return (date.fromisoformat(last_skipped_day) + timedelta(days=gap + 1)).isoformat()


def dismissed_until(today: str, *, daily: bool = False) -> str:
    gap = min(DISMISS_GAP, DAILY_MAX_GAP) if daily else DISMISS_GAP
    return (date.fromisoformat(today) + timedelta(days=gap + 1)).isoformat()
