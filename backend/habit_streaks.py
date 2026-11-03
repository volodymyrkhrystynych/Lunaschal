"""Streaks for daily tasks — the habit half of the Todo tab.

Pure date math over the 4am day keys `daily_task_completions.date` holds, so
it is tested without a database. Mirrored on the phone by
`apple/LunaschalCore/.../HabitStreaks.swift`, which recomputes a streak the
moment a tick is queued rather than waiting for the server to say so.

Two rules worth knowing:

- **Today unchecked does not break a streak.** The day is not over, so the
  current streak runs back from yesterday until today is ticked; only a whole
  missed day ends it.
- **History is a window, the longest streak is not.** The client is sent the
  done days of the last `HISTORY_DAYS` (enough for the grid and the widget's
  week), but `longest` is computed over every completion ever recorded.
"""
from datetime import date, timedelta
from typing import Iterable

# Twelve weeks: the phone's history grid, and more than the widget's week.
HISTORY_DAYS = 84


def _run_back(days: set[date], start: date) -> int:
    count = 0
    day = start
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


def current_streak(done: Iterable[str], today: str) -> int:
    """Consecutive done days ending today, or yesterday while today is open."""
    days = {date.fromisoformat(d) for d in done}
    start = date.fromisoformat(today)
    if start not in days:
        start -= timedelta(days=1)
    return _run_back(days, start)


def longest_streak(done: Iterable[str]) -> int:
    days = sorted({date.fromisoformat(d) for d in done})
    best = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def recent(done: Iterable[str], today: str, days: int = HISTORY_DAYS) -> list[str]:
    """The done days inside the `days`-long window ending today, oldest first.
    A day after today (a clock that ran ahead) is left out."""
    end = date.fromisoformat(today)
    start = end - timedelta(days=days - 1)
    return sorted({d for d in done if start <= date.fromisoformat(d) <= end})


def summary(done: Iterable[str], today: str) -> dict:
    """What `GET /api/tasks` adds to each task."""
    done = list(done)
    return {
        'streak': current_streak(done, today),
        'longest_streak': longest_streak(done),
        'history': recent(done, today),
    }
