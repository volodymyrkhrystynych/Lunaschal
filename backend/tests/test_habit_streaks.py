"""Streak math for daily tasks (`backend/habit_streaks.py`)."""
from backend.habit_streaks import (
    HISTORY_DAYS, current_streak, longest_streak, recent, summary,
)


def test_no_completions_is_no_streak():
    assert current_streak([], '2026-11-02') == 0
    assert longest_streak([]) == 0


def test_a_run_ending_today_counts_today():
    assert current_streak(['2026-10-31', '2026-11-01', '2026-11-02'], '2026-11-02') == 3


def test_today_unchecked_keeps_yesterdays_streak():
    assert current_streak(['2026-10-31', '2026-11-01'], '2026-11-02') == 2


def test_a_whole_missed_day_ends_the_streak():
    assert current_streak(['2026-10-30', '2026-10-31'], '2026-11-02') == 0


def test_a_gap_splits_the_run():
    done = ['2026-10-25', '2026-10-26', '2026-10-27', '2026-10-28', '2026-11-01', '2026-11-02']
    assert current_streak(done, '2026-11-02') == 2
    assert longest_streak(done) == 4


def test_runs_cross_month_and_year_boundaries():
    assert longest_streak(['2025-12-30', '2025-12-31', '2026-01-01']) == 3
    assert current_streak(['2026-02-28', '2026-03-01'], '2026-03-01') == 2


def test_duplicates_and_order_do_not_matter():
    assert longest_streak(['2026-11-02', '2026-11-01', '2026-11-02']) == 2


def test_a_day_after_today_does_not_extend_the_streak():
    # A completion dated after today (a clock that ran ahead) is ignored.
    assert current_streak(['2026-11-03'], '2026-11-02') == 0
    assert recent(['2026-11-03', '2026-11-02'], '2026-11-02') == ['2026-11-02']


def test_history_is_the_window_but_longest_is_all_time():
    old = ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05']
    s = summary(old + ['2026-11-02'], '2026-11-02')
    assert s == {'streak': 1, 'longest_streak': 5, 'history': ['2026-11-02']}


def test_the_window_includes_its_first_day_and_not_the_one_before():
    assert HISTORY_DAYS == 84
    # 2026-11-02 minus 83 days is 2026-08-11.
    assert recent(['2026-08-10', '2026-08-11'], '2026-11-02') == ['2026-08-11']
