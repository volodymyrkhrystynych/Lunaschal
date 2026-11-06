"""Goal horizons and the calendar periods they cover. Pure and DB-free.

Every argument is a 4am day key (`backend/day_boundary.py`), never a wall-clock
date: a week goal set at 01:30 on a Monday still belongs to the week that ends
that Sunday night. Weeks run Monday–Sunday; quarters start in Jan/Apr/Jul/Oct.
"""
from datetime import date, timedelta

HORIZONS = ('overarching', 'year', 'quarter', 'month', 'week')
PERIOD_HORIZONS = ('week', 'month', 'quarter', 'year')


def _month_end(year: int, month: int) -> date:
    first_next = date(year + month // 12, month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def period_bounds(horizon: str, day_key: str) -> tuple[str, str]:
    """First and last day key of the period containing `day_key`."""
    day = date.fromisoformat(day_key)
    if horizon == 'week':
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
    elif horizon == 'month':
        start = day.replace(day=1)
        end = _month_end(day.year, day.month)
    elif horizon == 'quarter':
        first_month = (day.month - 1) // 3 * 3 + 1
        start = date(day.year, first_month, 1)
        end = _month_end(day.year, first_month + 2)
    elif horizon == 'year':
        start, end = date(day.year, 1, 1), date(day.year, 12, 31)
    else:
        raise ValueError(f'{horizon!r} has no period')
    return start.isoformat(), end.isoformat()


def label(horizon: str, period_start: str | None) -> str:
    if horizon == 'overarching' or not period_start:
        return 'Overarching'
    start = date.fromisoformat(period_start)
    if horizon == 'week':
        return f'Week of {start.strftime("%b")} {start.day}'
    if horizon == 'month':
        return start.strftime('%B %Y')
    if horizon == 'quarter':
        return f'Q{(start.month - 1) // 3 + 1} {start.year}'
    return str(start.year)


def horizon_for_days(target_days: int | None) -> str:
    """Which horizon a pre-horizon streak goal belongs to, by its length."""
    days = target_days or 7
    if days <= 7:
        return 'week'
    if days <= 31:
        return 'month'
    if days <= 92:
        return 'quarter'
    return 'year'


def goal_period(horizon: str, start_date: str, target_days: int | None) -> tuple[str, str]:
    """The period a goal is graded over.

    A streak goal started late in its period still gets its whole streak: the
    period is stretched to cover `target_days` from its start, so a seven-day
    goal begun on a Friday is graded the following Thursday, not cut off on
    Sunday with no way to have won.
    """
    start, end = period_bounds(horizon, start_date)
    if target_days:
        streak_end = (date.fromisoformat(start_date) + timedelta(days=target_days - 1)).isoformat()
        end = max(end, streak_end)
    return start, end


def is_over(period_end: str | None, today: str) -> bool:
    return bool(period_end) and today > period_end
