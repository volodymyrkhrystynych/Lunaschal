"""Tests for the schedule-aware chat system prompt.

`build_chat_system_prompt` should append the user's calendar for today and the
next few days, with recurring series expanded — that's what lets the assistant
know when the user is at work rather than free.
"""
import time
from datetime import datetime

from backend.db import connection
from backend.ai.chat import (
    SCHEDULE_LOOKAHEAD_DAYS,
    SCHEDULE_MAX_EVENTS,
    SYSTEM_PROMPT,
    build_chat_system_prompt,
    format_schedule_context,
    get_upcoming_schedule,
)

# 2026-07-14 is a Tuesday (weekday 2, Sunday=0).
NOW = int(datetime(2026, 7, 14, 10, 0).timestamp())


def _insert_event(id, title, date, time_=None, end_time=None, description=None,
                  freq=None, interval=None, byweekday=None, until=None):
    connection.get_db().execute(
        '''INSERT INTO calendar_events(
               id, title, description, date, time, end_time, created_at,
               repeat_freq, repeat_interval, repeat_byweekday, repeat_until)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (id, title, description, date, time_, end_time, int(time.time()),
         freq, interval, byweekday, until),
    )
    connection.get_db().commit()


def test_no_events_leaves_the_prompt_alone(client):
    assert build_chat_system_prompt(NOW) == SYSTEM_PROMPT


def test_one_off_event_appears(client):
    _insert_event('e1', 'Dentist', '2026-07-15', '11:00', description='Cleaning')
    prompt = build_chat_system_prompt(NOW)
    assert prompt.startswith(SYSTEM_PROMPT)
    assert '- tomorrow 11:00: Dentist — Cleaning' in prompt


def test_recurring_event_appears_on_every_matching_day(client):
    _insert_event('w', 'Work', '2026-07-01', '09:00', '17:00',
                  freq='weekly', interval=1, byweekday='1,2,3,4,5')
    prompt = build_chat_system_prompt(NOW)
    # Tue 14th (today) through Fri 17th — the lookahead is 3 days.
    assert '- today 09:00–17:00: Work' in prompt
    assert '- tomorrow 09:00–17:00: Work' in prompt
    assert prompt.count('Work') == SCHEDULE_LOOKAHEAD_DAYS + 1


def test_weekend_gap_in_a_weekday_series(client):
    """Sat/Sun must be absent — the assistant shouldn't think work never stops."""
    _insert_event('w', 'Work', '2026-07-01', '09:00', '17:00',
                  freq='weekly', byweekday='1,2,3,4,5')
    # Friday the 17th: the lookahead reaches Mon the 20th over the weekend.
    friday = int(datetime(2026, 7, 17, 10, 0).timestamp())
    events = get_upcoming_schedule(friday)
    assert [e['date'] for e in events] == ['2026-07-17', '2026-07-20']


def test_events_beyond_the_horizon_are_excluded(client):
    _insert_event('e1', 'Far off', '2026-08-30', '11:00')
    assert build_chat_system_prompt(NOW) == SYSTEM_PROMPT


def test_past_events_are_excluded(client):
    _insert_event('e1', 'Yesterday thing', '2026-07-13', '11:00')
    assert build_chat_system_prompt(NOW) == SYSTEM_PROMPT


def test_skipped_occurrence_is_absent(client):
    _insert_event('w', 'Work', '2026-07-01', '09:00', '17:00',
                  freq='weekly', byweekday='1,2,3,4,5')
    connection.get_db().execute(
        '''INSERT INTO calendar_event_exceptions(id, event_id, date, action, created_at)
           VALUES ('x','w','2026-07-15','skip',0)''')
    connection.get_db().commit()
    assert [e['date'] for e in get_upcoming_schedule(NOW)] == [
        '2026-07-14', '2026-07-16', '2026-07-17']


def test_event_cap(client):
    for i in range(SCHEDULE_MAX_EVENTS + 5):
        _insert_event(f'e{i:02d}', f'Event {i}', '2026-07-15', f'{i % 24:02d}:00')
    assert len(get_upcoming_schedule(NOW)) == SCHEDULE_MAX_EVENTS


def test_all_day_event_has_no_time_span(client):
    _insert_event('e1', 'Holiday', '2026-07-15')
    assert '- tomorrow: Holiday' in build_chat_system_prompt(NOW)


def test_distant_days_use_a_dated_label(client):
    _insert_event('e1', 'Review', '2026-07-17', '10:00')
    assert '- Fri Jul 17 10:00: Review' in build_chat_system_prompt(NOW)


def test_format_schedule_context_is_empty_without_events():
    assert format_schedule_context([], NOW) == ''


def test_journal_and_schedule_both_appear(client):
    connection.get_db().execute(
        'INSERT INTO journal_entries (id, content, created_at, updated_at) VALUES (?,?,?,?)',
        ('j1', 'Slept badly again.', NOW - 3600, NOW - 3600),
    )
    _insert_event('e1', 'Dentist', '2026-07-15', '11:00')
    prompt = build_chat_system_prompt(NOW)
    assert 'Slept badly again.' in prompt
    assert 'Dentist' in prompt
    # Journal first, then schedule — matching the order in the system prompt's
    # own instructions.
    assert prompt.index('Slept badly again.') < prompt.index('Dentist')
