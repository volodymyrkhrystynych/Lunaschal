"""Unit tests for the pure habit streak/completion math in backend/habit_stats.py.

Fixed anchor: TODAY = Wednesday 2026-07-08 (ISO week starts Monday 2026-07-06).
"""
from datetime import date, timedelta

from backend.habit_stats import compute_stats

TODAY = date(2026, 7, 8)  # Wednesday
CREATED = date(2026, 6, 1)  # Monday, well before the 30-day window


def day(n: int) -> date:
    return TODAY - timedelta(days=n)


def habit(**kw) -> dict:
    base = {
        'type': 'boolean',
        'target_value': None,
        'schedule_type': 'daily',
        'schedule_days': None,
        'times_per_week': None,
        'created': CREATED,
    }
    base.update(kw)
    return base


def done(d: date, value=None) -> dict:
    return {'date': d, 'status': 'done', 'value': value}


def skip(d: date) -> dict:
    return {'date': d, 'status': 'skipped', 'value': None}


# --- daily schedule ---

def test_daily_consecutive_run():
    stats = compute_stats(habit(), [done(day(2)), done(day(1)), done(day(0))], TODAY)
    assert stats['current_streak'] == 3
    assert stats['longest_streak'] == 3
    assert stats['streak_unit'] == 'days'
    assert stats['today_status'] == 'done'
    assert stats['today_satisfied'] is True


def test_daily_gap_resets_current_but_longest_remembers():
    checks = [done(day(4)), done(day(3)), done(day(2)), done(day(0))]
    stats = compute_stats(habit(), checks, TODAY)
    assert stats['current_streak'] == 1
    assert stats['longest_streak'] == 3


def test_daily_today_unchecked_is_neutral():
    stats = compute_stats(habit(), [done(day(2)), done(day(1))], TODAY)
    assert stats['current_streak'] == 2
    assert stats['today_status'] == 'none'


def test_daily_skip_bridges_without_counting():
    stats = compute_stats(habit(), [done(day(2)), skip(day(1)), done(day(0))], TODAY)
    assert stats['current_streak'] == 2
    assert stats['longest_streak'] == 2


def test_daily_all_skips_is_zero():
    stats = compute_stats(habit(), [skip(day(2)), skip(day(1)), skip(day(0))], TODAY)
    assert stats['current_streak'] == 0
    assert stats['longest_streak'] == 0


def test_daily_no_checks():
    stats = compute_stats(habit(), [], TODAY)
    assert stats['current_streak'] == 0
    assert stats['longest_streak'] == 0
    assert stats['today_status'] == 'none'
    assert stats['today_satisfied'] is False


def test_daily_creation_date_floor():
    h = habit(created=day(2))
    stats = compute_stats(h, [done(day(2)), done(day(1)), done(day(0))], TODAY)
    assert stats['current_streak'] == 3
    assert stats['longest_streak'] == 3


# --- quantity habits ---

def q_habit(**kw) -> dict:
    return habit(type='quantity', target_value=10, **kw)


def test_quantity_meeting_target_satisfies():
    stats = compute_stats(q_habit(), [done(day(0), value=10)], TODAY)
    assert stats['current_streak'] == 1
    assert stats['today_satisfied'] is True
    assert stats['today_value'] == 10


def test_quantity_exceeding_target_satisfies():
    stats = compute_stats(q_habit(), [done(day(0), value=15)], TODAY)
    assert stats['today_satisfied'] is True


def test_quantity_partial_yesterday_breaks():
    checks = [done(day(2), value=10), done(day(1), value=5), done(day(0), value=10)]
    stats = compute_stats(q_habit(), checks, TODAY)
    assert stats['current_streak'] == 1
    assert stats['longest_streak'] == 1


def test_quantity_partial_today_is_neutral():
    checks = [done(day(2), value=10), done(day(1), value=10), done(day(0), value=5)]
    stats = compute_stats(q_habit(), checks, TODAY)
    assert stats['current_streak'] == 2
    assert stats['today_satisfied'] is False
    assert stats['today_status'] == 'done'
    assert stats['today_value'] == 5


# --- weekdays schedule (Mon/Wed/Fri = 0, 2, 4) ---

def mwf_habit(**kw) -> dict:
    return habit(schedule_type='weekdays', schedule_days=[0, 2, 4], **kw)


def test_weekdays_unscheduled_days_dont_break():
    # Wed Jul 1, Fri Jul 3, Mon Jul 6, Wed Jul 8 — spans weekend and Tue/Thu
    checks = [done(date(2026, 7, 1)), done(date(2026, 7, 3)),
              done(date(2026, 7, 6)), done(TODAY)]
    stats = compute_stats(mwf_habit(), checks, TODAY)
    assert stats['current_streak'] == 4
    assert stats['today_scheduled'] is True


def test_weekdays_missed_scheduled_day_breaks():
    # Missing Fri Jul 3
    checks = [done(date(2026, 7, 1)), done(date(2026, 7, 6)), done(TODAY)]
    stats = compute_stats(mwf_habit(), checks, TODAY)
    assert stats['current_streak'] == 2


def test_weekdays_today_unscheduled():
    tue = date(2026, 7, 7)
    checks = [done(date(2026, 7, 3)), done(date(2026, 7, 6))]
    stats = compute_stats(mwf_habit(), checks, tue)
    assert stats['current_streak'] == 2
    assert stats['today_scheduled'] is False


def test_weekdays_skipped_scheduled_day_bridges():
    checks = [done(date(2026, 7, 3)), skip(date(2026, 7, 6)), done(TODAY)]
    stats = compute_stats(mwf_habit(), checks, TODAY)
    assert stats['current_streak'] == 2


# --- per_week schedule (3x per week) ---

def pw_habit(**kw) -> dict:
    return habit(schedule_type='per_week', times_per_week=3, **kw)


def full_week(monday: date, n: int = 3) -> list[dict]:
    return [done(monday + timedelta(days=i)) for i in range(n)]


def test_per_week_counts_weeks_current_week_neutral():
    checks = full_week(date(2026, 6, 22)) + full_week(date(2026, 6, 29)) + [done(date(2026, 7, 6))]
    stats = compute_stats(pw_habit(created=date(2026, 6, 22)), checks, TODAY)
    assert stats['current_streak'] == 2
    assert stats['streak_unit'] == 'weeks'


def test_per_week_current_week_counts_once_satisfied():
    checks = (full_week(date(2026, 6, 22)) + full_week(date(2026, 6, 29))
              + full_week(date(2026, 7, 6)))
    stats = compute_stats(pw_habit(created=date(2026, 6, 22)), checks, TODAY)
    assert stats['current_streak'] == 3


def test_per_week_unsatisfied_past_week_breaks():
    checks = full_week(date(2026, 6, 29), n=2) + full_week(date(2026, 7, 6))
    stats = compute_stats(pw_habit(created=date(2026, 6, 29)), checks, TODAY)
    assert stats['current_streak'] == 1


def test_per_week_skip_lowers_requirement():
    week2 = [done(date(2026, 6, 29)), done(date(2026, 6, 30)), skip(date(2026, 7, 1))]
    checks = full_week(date(2026, 6, 22)) + week2 + full_week(date(2026, 7, 6))
    stats = compute_stats(pw_habit(created=date(2026, 6, 22)), checks, TODAY)
    assert stats['current_streak'] == 3


def test_per_week_fully_skipped_week_is_satisfied():
    week2 = [skip(date(2026, 6, 29)), skip(date(2026, 6, 30)), skip(date(2026, 7, 1))]
    checks = full_week(date(2026, 6, 22)) + week2 + full_week(date(2026, 7, 6))
    stats = compute_stats(pw_habit(created=date(2026, 6, 22)), checks, TODAY)
    assert stats['current_streak'] == 3


def test_per_week_longest_across_broken_week():
    checks = (full_week(date(2026, 6, 1)) + full_week(date(2026, 6, 8))
              + full_week(date(2026, 6, 22)))  # week of Jun 15 empty
    stats = compute_stats(pw_habit(), checks, TODAY)
    assert stats['longest_streak'] == 2
    assert stats['current_streak'] == 0


# --- completion over last 30 days ---

def test_completion_daily_counts_since_creation_with_today_grace():
    h = habit(created=day(9))  # 10 scheduled days in window incl today
    checks = [done(day(n)) for n in range(1, 6)]  # 5 done, today unchecked
    stats = compute_stats(h, checks, TODAY)
    assert stats['completion_30'] == round(5 / 9 * 100)


def test_completion_daily_excludes_skips():
    h = habit(created=day(9))
    checks = [done(day(n)) for n in range(1, 6)] + [skip(day(6)), skip(day(7))]
    stats = compute_stats(h, checks, TODAY)
    assert stats['completion_30'] == round(5 / 7 * 100)


def test_completion_weekdays_counts_scheduled_only():
    h = habit(schedule_type='weekdays', schedule_days=[2])  # Wednesdays
    # Wednesdays in window (Jun 9..Jul 8): Jun 10, 17, 24, Jul 1, Jul 8 (today, unchecked)
    checks = [done(date(2026, 6, 10)), done(date(2026, 6, 24))]
    stats = compute_stats(h, checks, TODAY)
    assert stats['completion_30'] == 50


def test_completion_per_week_prorated():
    h = pw_habit()  # 3x/week, window 30 days -> expected 90/7
    checks = [done(day(n)) for n in range(1, 11)]  # 10 satisfied days
    stats = compute_stats(h, checks, TODAY)
    assert stats['completion_30'] == round(10 / (3 * 30 / 7) * 100)


def test_completion_null_when_no_expectation():
    h = habit(created=TODAY)
    stats = compute_stats(h, [], TODAY)
    assert stats['completion_30'] is None


def test_completion_capped_at_100():
    h = habit(schedule_type='per_week', times_per_week=1)
    checks = [done(day(n)) for n in range(0, 10)]
    stats = compute_stats(h, checks, TODAY)
    assert stats['completion_30'] == 100


def test_today_skipped_status():
    stats = compute_stats(habit(), [done(day(1)), skip(day(0))], TODAY)
    assert stats['today_status'] == 'skipped'
    assert stats['today_satisfied'] is False
    assert stats['current_streak'] == 1
