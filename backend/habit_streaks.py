"""Streaks for daily tasks — the habit half of the Todo tab.

Pure date math over the 4am day keys `daily_task_completions.date` holds, so
it is tested without a database. Mirrored on the phone by
`apple/LunaschalCore/.../Todos.swift`'s `HabitStreaks`, which recomputes a
streak the moment a tick is queued rather than waiting for the server to say
so. Both run the same cases from `backend/tests/fixtures/habit_streaks.json`.

The rules, and why:

- **Never miss twice.** One missed day between done days is grace — the run
  survives it, though the day doesn't count — and two missed days in a row end
  it. Missing a single day barely dents habit formation (Lally et al. 2010);
  what costs is the second, and a streak that resets on one miss teaches
  people to give up after it. `at_risk` says the grace has been spent:
  yesterday was missed, so today is the one that can't be.
- **An excused day neither counts nor breaks.** A day the user told the agent
  was out of their hands (`day_excuses`, external or one-off) is neutral: it
  doesn't extend the run, doesn't reset the miss count, and doesn't end it.
  A streak that is lost to a hospital visit is how people stop trusting one.
- **Today unchecked does not break a streak.** The day is not over, so the
  run is counted back from yesterday until today is ticked.
- **History is a window, the longest streak is not.** The client is sent the
  done days of the last `HISTORY_DAYS`, but `longest` is computed over every
  completion ever recorded.
"""
from datetime import date, timedelta
from typing import Iterable

# Twelve weeks: the phone's history grid, and more than the widget's week.
HISTORY_DAYS = 84
# Missed days in a row a run survives.
GRACE = 1


def _dates(days: Iterable[str]) -> set[date]:
    return {date.fromisoformat(d) for d in days}


def _walk_back(done: set[date], excused: set[date], start: date) -> int:
    if not done:
        return 0
    floor = min(done)
    count = misses = 0
    day = start
    while day >= floor:
        if day in done:
            count += 1
            misses = 0
        elif day not in excused:
            misses += 1
            if misses > GRACE:
                break
        day -= timedelta(days=1)
    return count


def current_streak(done: Iterable[str], today: str, excused: Iterable[str] = ()) -> int:
    """Done days in the run reaching today, or yesterday while today is open."""
    end = date.fromisoformat(today)
    days = {d for d in _dates(done) if d <= end}
    start = end if end in days else end - timedelta(days=1)
    return _walk_back(days, _dates(excused), start)


def at_risk(done: Iterable[str], today: str, excused: Iterable[str] = ()) -> bool:
    """A live run whose grace day is spent: yesterday was missed, unexcused,
    and today isn't done yet."""
    end = date.fromisoformat(today)
    days, off = _dates(done), _dates(excused)
    yesterday = end - timedelta(days=1)
    if end in days or yesterday in days or yesterday in off:
        return False
    return current_streak(done, today, excused) > 0


def longest_streak(done: Iterable[str], excused: Iterable[str] = ()) -> int:
    days, off = _dates(done), _dates(excused)
    if not days:
        return 0
    best = run = misses = 0
    day, last = min(days), max(days)
    while day <= last:
        if day in days:
            run += 1
            misses = 0
            best = max(best, run)
        elif day not in off:
            misses += 1
            if misses > GRACE:
                run = 0
        day += timedelta(days=1)
    return best


def recent(done: Iterable[str], today: str, days: int = HISTORY_DAYS) -> list[str]:
    """The days inside the `days`-long window ending today, oldest first.
    A day after today (a clock that ran ahead) is left out."""
    end = date.fromisoformat(today)
    start = end - timedelta(days=days - 1)
    return sorted({d for d in done if start <= date.fromisoformat(d) <= end})


def summary(done: Iterable[str], today: str, excused: Iterable[str] = ()) -> dict:
    """What `GET /api/tasks` adds to each task."""
    done, excused = list(done), list(excused)
    return {
        'streak': current_streak(done, today, excused),
        'longest_streak': longest_streak(done, excused),
        'at_risk': at_risk(done, today, excused),
        'history': recent(done, today),
        'excused': recent(excused, today),
    }
