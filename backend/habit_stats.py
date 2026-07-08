"""Pure streak/completion statistics for habits. No DB access.

`habit` dicts use the keys: type ('boolean'|'quantity'), target_value,
schedule_type ('daily'|'weekdays'|'per_week'), schedule_days (list of ints,
0=Mon..6=Sun), times_per_week, created (date the habit was created).

`checks` are dicts with: date (date), status ('done'|'skipped'), value.
"""
from datetime import date, timedelta


def _is_satisfied(habit: dict, check: dict | None) -> bool:
    if check is None or check['status'] != 'done':
        return False
    if habit['type'] == 'quantity':
        return (check['value'] or 0) >= (habit['target_value'] or 0)
    return True


def _is_scheduled(habit: dict, d: date) -> bool:
    if habit['schedule_type'] == 'weekdays':
        return d.weekday() in (habit['schedule_days'] or [])
    return True


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _day_streaks(habit: dict, by_date: dict, today: date, floor: date) -> tuple[int, int]:
    current = 0
    d = today
    while d >= floor:
        if _is_scheduled(habit, d):
            check = by_date.get(d)
            if check and check['status'] == 'skipped':
                pass  # a skipped day bridges the streak without counting
            elif _is_satisfied(habit, check):
                current += 1
            elif d == today:
                pass  # today isn't over yet — unchecked/partial doesn't break
            else:
                break
        d -= timedelta(days=1)

    longest = run = 0
    d = floor
    while d <= today:
        if _is_scheduled(habit, d):
            check = by_date.get(d)
            if check and check['status'] == 'skipped':
                pass
            elif _is_satisfied(habit, check):
                run += 1
                longest = max(longest, run)
            elif d != today:
                run = 0
        d += timedelta(days=1)
    return current, longest


def _week_streaks(habit: dict, by_date: dict, today: date, floor: date) -> tuple[int, int]:
    target = habit['times_per_week'] or 0
    satisfied_days: dict[date, int] = {}
    skipped_days: dict[date, int] = {}
    for d, check in by_date.items():
        week = _monday(d)
        if check['status'] == 'skipped':
            skipped_days[week] = skipped_days.get(week, 0) + 1
        elif _is_satisfied(habit, check):
            satisfied_days[week] = satisfied_days.get(week, 0) + 1

    def week_ok(week: date) -> bool:
        # A skip excuses one required day; a fully-skipped week is satisfied.
        effective = max(0, target - skipped_days.get(week, 0))
        return satisfied_days.get(week, 0) >= effective

    this_week = _monday(today)
    first_week = _monday(floor)

    current = 0
    week = this_week
    if week_ok(week):
        current += 1
    week -= timedelta(days=7)  # the incomplete current week never breaks
    while week >= first_week:
        if not week_ok(week):
            break
        current += 1
        week -= timedelta(days=7)

    longest = run = 0
    week = first_week
    while week <= this_week:
        if week_ok(week):
            run += 1
            longest = max(longest, run)
        elif week != this_week:
            run = 0
        week += timedelta(days=7)
    return current, longest


def _completion_30(habit: dict, by_date: dict, today: date) -> int | None:
    start = max(today - timedelta(days=29), habit['created'])
    if start > today:
        start = today

    if habit['schedule_type'] == 'per_week':
        window_days = (today - start).days + 1
        expected = (habit['times_per_week'] or 0) * window_days / 7
        satisfied = skips = 0
        for d, check in by_date.items():
            if start <= d <= today:
                if check['status'] == 'skipped':
                    skips += 1
                elif _is_satisfied(habit, check):
                    satisfied += 1
        expected = max(0.0, expected - skips)
        if expected <= 0:
            return None
        return min(100, round(satisfied / expected * 100))

    denom = num = 0
    d = start
    while d <= today:
        if _is_scheduled(habit, d):
            check = by_date.get(d)
            if check and check['status'] == 'skipped':
                pass
            elif d == today and check is None:
                pass  # grace: an unchecked today doesn't count against completion
            else:
                denom += 1
                if _is_satisfied(habit, check):
                    num += 1
        d += timedelta(days=1)
    if denom == 0:
        return None
    return round(num / denom * 100)


def compute_stats(habit: dict, checks: list[dict], today: date) -> dict:
    by_date = {c['date']: c for c in checks}
    floor = habit['created']
    if by_date:
        floor = min(floor, min(by_date))

    if habit['schedule_type'] == 'per_week':
        current, longest = _week_streaks(habit, by_date, today, floor)
        unit = 'weeks'
    else:
        current, longest = _day_streaks(habit, by_date, today, floor)
        unit = 'days'

    today_check = by_date.get(today)
    return {
        'current_streak': current,
        'longest_streak': longest,
        'streak_unit': unit,
        'completion_30': _completion_30(habit, by_date, today),
        'today_status': today_check['status'] if today_check else 'none',
        'today_value': today_check['value'] if today_check else None,
        'today_satisfied': _is_satisfied(habit, today_check),
        'today_scheduled': _is_scheduled(habit, today),
    }
