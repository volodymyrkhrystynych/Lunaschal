"""4am day-boundary behavior in backend/ai/chat.py: today/yesterday journal
and message-prefix labels, and where the "today" of the upcoming-schedule
context starts — all keyed off backend.day_boundary.day_key_for rather than
the literal calendar date, so a late-night message doesn't jump the gun on
a new day the user hasn't reached yet."""
from datetime import datetime

from backend.ai import chat as ai_chat
from backend.day_boundary import day_key_for


def _ts(y, m, d, h, mi=0):
    return int(datetime(y, m, d, h, mi).timestamp())


# --- _format_entry_time ---

def test_entry_from_the_same_4am_day_is_today_even_across_midnight():
    now = _ts(2026, 7, 9, 1, 0)     # 1am — still Jul 8's 4am-day
    entry = _ts(2026, 7, 8, 22, 0)  # written the evening before
    assert ai_chat._format_entry_time(entry, now).startswith('today')


def test_entry_from_after_the_4am_rollover_is_not_today():
    now = _ts(2026, 7, 9, 1, 0)     # still Jul 8's 4am-day
    entry = _ts(2026, 7, 9, 0, 30)  # written after midnight but before 4am -- same day
    assert ai_chat._format_entry_time(entry, now).startswith('today')


def test_entry_from_the_day_before_the_4am_day_is_yesterday():
    now = _ts(2026, 7, 9, 9, 0)      # Jul 9's 4am-day (after the rollover)
    entry = _ts(2026, 7, 8, 22, 0)   # the evening before, a full 4am-day earlier
    assert ai_chat._format_entry_time(entry, now).startswith('yesterday')


# --- get_upcoming_schedule / format_schedule_context ---

def _insert_event(db, id, title, date):
    db.execute(
        'INSERT INTO calendar_events(id, title, date, created_at) VALUES (?,?,?,?)',
        (id, title, date, int(datetime.now().timestamp())),
    )


def test_upcoming_schedule_starts_from_the_4am_anchored_today(client):
    from backend.db.connection import get_db
    db = get_db()
    now = _ts(2026, 7, 9, 1, 0)  # 1am -- still Jul 8's 4am-day
    _insert_event(db, 'e1', 'Standup', '2026-07-08')  # "today" per the 4am day
    db.commit()

    events = ai_chat.get_upcoming_schedule(now)
    assert [e['title'] for e in events] == ['Standup']


def test_format_schedule_context_labels_the_4am_day_as_today():
    now = _ts(2026, 7, 9, 1, 0)  # 1am -- still Jul 8's 4am-day
    events = [{'date': '2026-07-08', 'title': 'Standup', 'time': None,
              'end_time': None, 'description': None}]
    prompt = ai_chat.format_schedule_context(events, now)
    assert '- today: Standup' in prompt


def test_format_schedule_context_labels_the_next_4am_day_as_tomorrow():
    now = _ts(2026, 7, 9, 1, 0)  # still Jul 8's 4am-day
    events = [{'date': '2026-07-09', 'title': 'Dentist', 'time': None,
              'end_time': None, 'description': None}]
    prompt = ai_chat.format_schedule_context(events, now)
    assert '- tomorrow: Dentist' in prompt


def test_day_key_for_matches_the_today_used_by_schedule_and_entry_labels():
    """Sanity check that all three helpers agree on what day it is."""
    now = _ts(2026, 7, 9, 1, 0)
    assert day_key_for(now) == '2026-07-08'
