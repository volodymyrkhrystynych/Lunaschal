"""Tests for the pure recurrence expander (no DB, no Flask).

Weekdays are Sunday=0 throughout, matching JS Date.getDay() and the UI grid.
2026-07-01 is a Wednesday (weekday 3), which several fixtures lean on.
"""
import pytest

from backend.calendar_recurrence import (
    MAX_OCCURRENCES,
    expand_events,
    format_byweekday,
    occurrence_dates,
    parse_byweekday,
)


def event(**kw):
    base = {'id': 'e1', 'title': 'Work', 'date': '2026-07-01',
            'time': '09:00', 'end_time': '17:00', 'repeat_freq': None,
            'repeat_interval': None, 'repeat_byweekday': None, 'repeat_until': None}
    base.update(kw)
    return base


# --- byweekday parsing ---

@pytest.mark.parametrize('csv,expected', [
    ('1,2,3,4,5', {1, 2, 3, 4, 5}),
    ('0', {0}),
    (' 1 , 6 ', {1, 6}),
    (None, set()),
    ('', set()),
    ('9,-1,abc', set()),      # out of range / non-numeric are dropped
    ('2,9,3', {2, 3}),
])
def test_parse_byweekday(csv, expected):
    assert parse_byweekday(csv) == expected


def test_format_byweekday_sorts_and_dedupes():
    assert format_byweekday({5, 1, 3, 1}) == '1,3,5'
    assert format_byweekday([]) is None
    assert format_byweekday(None) is None


# --- non-repeating events ---

def test_one_off_inside_window():
    assert occurrence_dates(event(), '2026-06-01', '2026-07-31') == ['2026-07-01']


def test_one_off_outside_window():
    assert occurrence_dates(event(), '2026-08-01', '2026-08-31') == []


def test_unknown_freq_treated_as_one_off():
    assert occurrence_dates(event(repeat_freq='yearly'), '2026-06-01', '2026-07-31') == [
        '2026-07-01'
    ]


# --- daily ---

def test_daily_every_day():
    dates = occurrence_dates(
        event(repeat_freq='daily', repeat_interval=1), '2026-07-01', '2026-07-05')
    assert dates == ['2026-07-01', '2026-07-02', '2026-07-03', '2026-07-04', '2026-07-05']


def test_daily_interval_anchors_on_event_date():
    # Every 3 days from Jul 1 -> 1, 4, 7, 10 … the window start must not reset
    # the cycle, so a window opening on Jul 5 still yields Jul 7 and Jul 10.
    dates = occurrence_dates(
        event(repeat_freq='daily', repeat_interval=3), '2026-07-05', '2026-07-11')
    assert dates == ['2026-07-07', '2026-07-10']


def test_daily_never_precedes_the_anchor():
    dates = occurrence_dates(
        event(repeat_freq='daily'), '2026-06-25', '2026-07-02')
    assert dates == ['2026-07-01', '2026-07-02']


# --- weekly ---

def test_weekly_weekday_set():
    """The canonical case: work Mon-Fri."""
    dates = occurrence_dates(
        event(repeat_freq='weekly', repeat_interval=1, repeat_byweekday='1,2,3,4,5'),
        '2026-07-06', '2026-07-12')
    assert dates == ['2026-07-06', '2026-07-07', '2026-07-08', '2026-07-09', '2026-07-10']


def test_weekly_without_byweekday_uses_the_anchor_weekday():
    # 2026-07-01 is a Wednesday, so the series is every Wednesday.
    dates = occurrence_dates(
        event(repeat_freq='weekly'), '2026-07-01', '2026-07-22')
    assert dates == ['2026-07-01', '2026-07-08', '2026-07-15', '2026-07-22']


def test_weekly_interval_skips_off_weeks():
    # Every other Wednesday from Jul 1.
    dates = occurrence_dates(
        event(repeat_freq='weekly', repeat_interval=2), '2026-07-01', '2026-08-01')
    assert dates == ['2026-07-01', '2026-07-15', '2026-07-29']


def test_weekly_interval_anchored_across_a_later_window():
    # The fortnightly cycle keeps its phase when the window opens much later.
    dates = occurrence_dates(
        event(repeat_freq='weekly', repeat_interval=2), '2026-08-02', '2026-08-31')
    assert dates == ['2026-08-12', '2026-08-26']


def test_weekly_occurrences_before_the_anchor_are_dropped():
    # Mon-Fri rule anchored on Wednesday Jul 1: that week's Mon/Tue predate it.
    dates = occurrence_dates(
        event(repeat_freq='weekly', repeat_byweekday='1,2,3,4,5'),
        '2026-06-28', '2026-07-03')
    assert dates == ['2026-07-01', '2026-07-02', '2026-07-03']


# --- monthly ---

def test_monthly_same_day_each_month():
    dates = occurrence_dates(
        event(date='2026-01-15', repeat_freq='monthly'), '2026-01-01', '2026-04-30')
    assert dates == ['2026-01-15', '2026-02-15', '2026-03-15', '2026-04-15']


def test_monthly_clamps_short_months():
    dates = occurrence_dates(
        event(date='2026-01-31', repeat_freq='monthly'), '2026-01-01', '2026-04-30')
    assert dates == ['2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30']


def test_monthly_interval():
    dates = occurrence_dates(
        event(date='2026-01-10', repeat_freq='monthly', repeat_interval=3),
        '2026-01-01', '2026-12-31')
    assert dates == ['2026-01-10', '2026-04-10', '2026-07-10', '2026-10-10']


# --- yearly ---

def test_yearly_same_day_each_year():
    dates = occurrence_dates(
        event(date='1990-03-14', repeat_freq='yearly'), '2026-01-01', '2029-12-31')
    assert dates == ['2026-03-14', '2027-03-14', '2028-03-14', '2029-03-14']


def test_yearly_never_precedes_the_anchor():
    dates = occurrence_dates(
        event(date='2028-03-14', repeat_freq='yearly'), '2026-01-01', '2029-12-31')
    assert dates == ['2028-03-14', '2029-03-14']


def test_yearly_interval():
    dates = occurrence_dates(
        event(date='2026-06-01', repeat_freq='yearly', repeat_interval=2),
        '2026-01-01', '2032-12-31')
    assert dates == ['2026-06-01', '2028-06-01', '2030-06-01', '2032-06-01']


def test_yearly_leap_day_clamps_to_feb_28_off_cycle():
    """A Feb 29 anchor still fires every year, clamped like _monthly does for
    the 31st — the birthday shows up rather than vanishing for three years."""
    dates = occurrence_dates(
        event(date='2024-02-29', repeat_freq='yearly'), '2025-01-01', '2028-12-31')
    assert dates == ['2025-02-28', '2026-02-28', '2027-02-28', '2028-02-29']


def test_yearly_found_from_a_narrow_window():
    """A one-day window years after the anchor still finds its occurrence."""
    dates = occurrence_dates(
        event(date='1990-12-25', repeat_freq='yearly'), '2026-12-25', '2026-12-25')
    assert dates == ['2026-12-25']


def test_yearly_respects_repeat_until():
    dates = occurrence_dates(
        event(date='2020-05-05', repeat_freq='yearly', repeat_until='2027-01-01'),
        '2025-01-01', '2030-12-31')
    assert dates == ['2025-05-05', '2026-05-05']


def test_yearly_occurrence_can_be_skipped_and_moved():
    ev = event(id='bday', date='1990-08-09', repeat_freq='yearly')
    excs = [
        {'event_id': 'bday', 'date': '2026-08-09', 'action': 'skip'},
        {'event_id': 'bday', 'date': '2027-08-09', 'action': 'move',
         'new_date': '2027-08-10', 'new_time': None, 'new_end_time': None},
    ]
    out = expand_events([ev], excs, '2026-01-01', '2028-12-31')
    assert [i['date'] for i in out] == ['2027-08-10', '2028-08-09']
    assert all(i['isRecurring'] for i in out)


# --- until ---

def test_repeat_until_is_inclusive():
    dates = occurrence_dates(
        event(repeat_freq='daily', repeat_until='2026-07-03'), '2026-07-01', '2026-07-31')
    assert dates == ['2026-07-01', '2026-07-02', '2026-07-03']


def test_repeat_until_before_window_yields_nothing():
    dates = occurrence_dates(
        event(repeat_freq='daily', repeat_until='2026-07-03'), '2026-07-10', '2026-07-31')
    assert dates == []


# --- malformed input ---

@pytest.mark.parametrize('kwargs,window', [
    ({'date': 'not-a-date'}, ('2026-07-01', '2026-07-31')),
    ({}, ('nonsense', '2026-07-31')),
    ({}, ('2026-07-31', '2026-07-01')),          # inverted window
])
def test_bad_input_yields_no_occurrences(kwargs, window):
    assert occurrence_dates(event(**kwargs), *window) == []


def test_zero_or_garbage_interval_falls_back_to_one():
    for interval in (0, -5, None, 'x'):
        dates = occurrence_dates(
            event(repeat_freq='daily', repeat_interval=interval),
            '2026-07-01', '2026-07-03')
        assert dates == ['2026-07-01', '2026-07-02', '2026-07-03']


def test_occurrence_cap():
    dates = occurrence_dates(
        event(repeat_freq='daily'), '2026-07-01', '2036-07-01')
    assert len(dates) == MAX_OCCURRENCES


# --- expand_events: instances, exceptions, sorting ---

def test_expand_marks_instances():
    out = expand_events([event(repeat_freq='daily')], [], '2026-07-01', '2026-07-02')
    assert [e['date'] for e in out] == ['2026-07-01', '2026-07-02']
    assert all(e['id'] == 'e1' for e in out)          # series id is preserved
    assert [e['occurrenceDate'] for e in out] == ['2026-07-01', '2026-07-02']
    assert all(e['isRecurring'] for e in out)


def test_expand_flags_one_offs_as_not_recurring():
    out = expand_events([event()], [], '2026-07-01', '2026-07-02')
    assert out[0]['isRecurring'] is False
    assert out[0]['occurrenceDate'] == '2026-07-01'


def test_skip_exception_drops_one_occurrence():
    excs = [{'event_id': 'e1', 'date': '2026-07-02', 'action': 'skip',
             'new_date': None, 'new_time': None, 'new_end_time': None}]
    out = expand_events([event(repeat_freq='daily')], excs, '2026-07-01', '2026-07-03')
    assert [e['date'] for e in out] == ['2026-07-01', '2026-07-03']


def test_move_exception_reschedules_one_occurrence():
    excs = [{'event_id': 'e1', 'date': '2026-07-02', 'action': 'move',
             'new_date': '2026-07-04', 'new_time': '13:00', 'new_end_time': '15:00'}]
    out = expand_events([event(repeat_freq='daily')], excs, '2026-07-01', '2026-07-04')
    moved = [e for e in out if e['occurrenceDate'] == '2026-07-02'][0]
    assert moved['date'] == '2026-07-04'
    assert (moved['time'], moved['end_time']) == ('13:00', '15:00')
    assert '2026-07-02' not in [e['date'] for e in out]


def test_move_pulls_an_occurrence_into_the_window():
    # The Jun 30 occurrence was moved to Jul 1; a window starting Jul 1 must
    # still show it even though its original date sits outside.
    excs = [{'event_id': 'e1', 'date': '2026-06-30', 'action': 'move',
             'new_date': '2026-07-01', 'new_time': None, 'new_end_time': None}]
    out = expand_events(
        [event(date='2026-06-01', repeat_freq='daily')], excs, '2026-07-01', '2026-07-01')
    assert [e['occurrenceDate'] for e in out] == ['2026-06-30', '2026-07-01']


def test_move_out_of_the_window_removes_it():
    excs = [{'event_id': 'e1', 'date': '2026-07-02', 'action': 'move',
             'new_date': '2026-08-15', 'new_time': None, 'new_end_time': None}]
    out = expand_events([event(repeat_freq='daily')], excs, '2026-07-01', '2026-07-03')
    assert [e['date'] for e in out] == ['2026-07-01', '2026-07-03']


def test_exceptions_are_scoped_to_their_series():
    excs = [{'event_id': 'other', 'date': '2026-07-02', 'action': 'skip',
             'new_date': None, 'new_time': None, 'new_end_time': None}]
    out = expand_events([event(repeat_freq='daily')], excs, '2026-07-01', '2026-07-03')
    assert len(out) == 3


def test_expand_sorts_by_date_then_time():
    a = event(id='a', date='2026-07-02', time='14:00')
    b = event(id='b', date='2026-07-01', time='08:00')
    c = event(id='c', date='2026-07-02', time='09:00')
    out = expand_events([a, b, c], [], '2026-07-01', '2026-07-03')
    assert [e['id'] for e in out] == ['b', 'c', 'a']


def test_expand_rejects_a_bad_window():
    assert expand_events([event()], [], 'bad', '2026-07-31') == []
