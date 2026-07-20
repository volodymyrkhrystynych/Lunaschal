"""Unit tests for the pure repeat-interval arithmetic (`backend/todo_recurrence.py`)."""
from datetime import datetime, timezone

import pytest

from backend.todo_recurrence import add_interval, next_due


def ts(year, month, day, hour=12, minute=30):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def test_add_interval_days_and_weeks():
    base = ts(2026, 7, 20)
    assert add_interval(base, 1, 'day') == base + 86400
    assert add_interval(base, 3, 'day') == base + 3 * 86400
    assert add_interval(base, 2, 'week') == base + 14 * 86400


def test_add_interval_month_keeps_day_and_time_when_valid():
    assert add_interval(ts(2026, 3, 15), 2, 'month') == ts(2026, 5, 15)


def test_add_interval_month_clamps_to_month_end():
    assert add_interval(ts(2026, 1, 31), 1, 'month') == ts(2026, 2, 28)
    # Leap year February keeps the 29th
    assert add_interval(ts(2024, 1, 31), 1, 'month') == ts(2024, 2, 29)


def test_add_interval_month_rolls_over_the_year():
    assert add_interval(ts(2026, 12, 15), 1, 'month') == ts(2027, 1, 15)
    assert add_interval(ts(2026, 11, 15), 14, 'month') == ts(2028, 1, 15)


def test_add_interval_rejects_unknown_unit():
    with pytest.raises(ValueError):
        add_interval(ts(2026, 1, 1), 1, 'fortnight')


def test_next_due_advances_a_future_due_by_one_interval():
    now = ts(2026, 7, 20)
    due = ts(2026, 7, 25)
    assert next_due(due, 1, 'week', now) == ts(2026, 8, 1)


def test_next_due_rolls_a_long_overdue_todo_past_now_keeping_the_anchor():
    # Monthly on the 15th, last due in March, completed in July: the next
    # due is the upcoming 15th, not a date still in the past.
    now = ts(2026, 7, 20)
    due = ts(2026, 3, 15)
    assert next_due(due, 1, 'month', now) == ts(2026, 8, 15)


def test_next_due_without_a_due_date_bases_on_now():
    now = ts(2026, 7, 20)
    assert next_due(None, 3, 'day', now) == now + 3 * 86400
