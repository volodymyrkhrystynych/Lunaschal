"""Wake/sleep derivation and the manual override behind /api/calendar/sleep.

The whole feature rests on one window — 04:00 -> 04:00 local, borrowed from
backend/day_boundary.py — so most of these tests are really about which side
of a boundary a timestamp falls on.
"""
import json
import time
from datetime import datetime

from ulid import ULID

from backend import sleep
from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db

DAY = '2026-07-08'


def at(date: str, hhmm: str) -> int:
    """Unix second for a local wall-clock time on a calendar date."""
    return int(datetime.fromisoformat(f'{date}T{hhmm}:00').timestamp())


def journal(ts: int) -> None:
    get_db().execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at) VALUES (?,?,?,?)',
        (str(ULID()), 'note', ts, ts),
    )
    get_db().commit()


def message(ts: int, role: str = 'user') -> None:
    db = get_db()
    conv_id = 'conv-1'
    db.execute(
        'INSERT OR IGNORE INTO conversations(id, created_at, updated_at) VALUES (?,?,?)',
        (conv_id, ts, ts),
    )
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES (?,?,?,?,?)',
        (str(ULID()), conv_id, role, 'hi', ts),
    )
    db.commit()


def transcription(ts: int) -> None:
    get_db().execute(
        'INSERT INTO transcriptions(id, text, source, created_at) VALUES (?,?,?,?)',
        (str(ULID()), 'spoken', 'paste', ts),
    )
    get_db().commit()


def food(ts: int) -> None:
    get_db().execute(
        'INSERT INTO food_entries(id, dish, created_at, updated_at) VALUES (?,?,?,?)',
        (str(ULID()), 'toast', ts, ts),
    )
    get_db().commit()


def calories(ts: int) -> None:
    get_db().execute(
        'INSERT INTO calorie_logs(id, date, description, calories, created_at) VALUES (?,?,?,?,?)',
        (str(ULID()), DAY, 'coke', 140, ts),
    )
    get_db().commit()


# A moment safely past the end of DAY's window, so the day counts as lived.
AFTER = at('2026-07-09', '12:00')


# --- the window ---

def test_the_day_runs_four_am_to_four_am():
    start, end = day_bounds(DAY)
    assert start == at(DAY, '04:00')
    assert end == at('2026-07-09', '04:00')
    # Every instant in the window maps back to the key it came from — the two
    # helpers have to agree or a derived time lands on the wrong day.
    assert day_key_for(start) == DAY
    assert day_key_for(end - 1) == DAY
    assert day_key_for(start - 1) == '2026-07-07'


def test_activity_before_four_am_belongs_to_the_previous_day():
    journal(at('2026-07-09', '02:00'))
    assert sleep.derive_window(DAY)[1] == at('2026-07-09', '02:00')
    assert sleep.derive_window('2026-07-09') == (None, None)


# --- derivation ---

def test_wake_and_sleep_are_the_first_and_last_activity_of_the_window():
    journal(at(DAY, '09:00'))
    journal(at(DAY, '22:00'))
    assert sleep.derive_window(DAY) == (at(DAY, '09:00'), at(DAY, '22:00'))


def test_every_signal_table_counts():
    # Each end comes from a different table, so a derivation that stopped at
    # the first table with a row would get both wrong.
    transcription(at(DAY, '07:10'))
    journal(at(DAY, '09:00'))
    message(at(DAY, '12:00'))
    food(at(DAY, '18:00'))
    calories(at(DAY, '23:30'))
    assert sleep.derive_window(DAY) == (at(DAY, '07:10'), at(DAY, '23:30'))


def test_an_assistant_reply_is_not_the_user_being_awake():
    message(at(DAY, '05:00'), role='assistant')
    message(at(DAY, '06:00'), role='system')
    assert sleep.derive_window(DAY) == (None, None)
    message(at(DAY, '07:00'), role='user')
    assert sleep.derive_window(DAY)[0] == at(DAY, '07:00')


def test_a_day_with_no_activity_stays_unknown():
    resolved = sleep.resolve_day(DAY, now=AFTER)
    assert resolved['wakeAt'] is None and resolved['sleepAt'] is None
    assert resolved['wakeSource'] is None and resolved['sleepSource'] is None
    assert get_db().execute('SELECT COUNT(*) c FROM sleep_logs').fetchone()['c'] == 0


# --- today is still in progress ---

def test_a_derived_sleep_time_is_withheld_until_the_day_is_over():
    journal(at(DAY, '09:00'))
    journal(at(DAY, '14:00'))
    live = sleep.resolve_day(DAY, now=at(DAY, '14:30'))
    # Two o'clock this afternoon is not a bedtime.
    assert live['wakeAt'] == at(DAY, '09:00')
    assert live['sleepAt'] is None
    assert sleep.resolve_day(DAY, now=AFTER)['sleepAt'] == at(DAY, '14:00')


def test_a_manual_sleep_time_shows_immediately():
    journal(at(DAY, '09:00'))
    sleep.set_day(DAY, wake=None, sleep=at(DAY, '23:00'))
    live = sleep.resolve_day(DAY, now=at(DAY, '14:30'))
    assert live['sleepAt'] == at(DAY, '23:00')
    assert live['sleepSource'] == 'manual'


# --- manual override ---

def test_a_manual_end_wins_while_the_other_stays_derived():
    journal(at(DAY, '09:00'))
    journal(at(DAY, '22:00'))
    sleep.set_day(DAY, wake=at(DAY, '07:15'), sleep=None)
    resolved = sleep.resolve_day(DAY, now=AFTER)
    assert (resolved['wakeAt'], resolved['wakeSource']) == (at(DAY, '07:15'), 'manual')
    assert (resolved['sleepAt'], resolved['sleepSource']) == (at(DAY, '22:00'), 'auto')


def test_clearing_restores_the_derived_value_rather_than_blanking_the_day():
    journal(at(DAY, '09:00'))
    sleep.set_day(DAY, wake=at(DAY, '07:15'), sleep=None)
    resolved = sleep.clear_day(DAY)
    assert (resolved['wakeAt'], resolved['wakeSource']) == (at(DAY, '09:00'), 'auto')
    assert get_db().execute('SELECT COUNT(*) c FROM sleep_logs').fetchone()['c'] == 0


def test_setting_a_day_twice_updates_one_row():
    sleep.set_day(DAY, wake=at(DAY, '07:00'), sleep=None)
    sleep.set_day(DAY, wake=at(DAY, '08:00'), sleep=None)
    rows = get_db().execute('SELECT wake_at FROM sleep_logs WHERE date=?', (DAY,)).fetchall()
    assert [r['wake_at'] for r in rows] == [at(DAY, '08:00')]


def test_a_time_before_the_rollover_lands_on_the_next_calendar_date():
    # "I went to sleep at 01:30 on Wednesday" means the small hours of Thursday;
    # storing it against Wednesday midnight would put bedtime 23 hours early.
    assert sleep.time_to_timestamp(DAY, '01:30') == at('2026-07-09', '01:30')
    assert sleep.time_to_timestamp(DAY, '23:30') == at(DAY, '23:30')
    assert sleep.time_to_timestamp(DAY, '04:00') == at(DAY, '04:00')


# --- routes ---

def get_sleep(client, date=DAY):
    resp = client.get(f'/api/calendar/sleep/{date}')
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def put_sleep(client, date=DAY, **body):
    return client.put(f'/api/calendar/sleep/{date}', data=json.dumps(body),
                      content_type='application/json')


def test_get_sleep_rejects_a_bad_date(client):
    assert client.get('/api/calendar/sleep/07-08-2026').status_code == 400


def test_put_sleep_rejects_a_bad_time(client):
    assert put_sleep(client, wake='9am').status_code == 400
    assert put_sleep(client, sleep='25:99').status_code == 400


def test_put_sleep_rejects_a_bedtime_before_the_wake(client):
    resp = put_sleep(client, wake='09:00', sleep='08:00')
    assert resp.status_code == 400
    assert 'after' in resp.get_json()['error']


def test_put_and_clear_round_trip(client):
    body = put_sleep(client, wake='07:20', sleep='23:40').get_json()
    assert body['wakeAt'] == at(DAY, '07:20')
    assert body['sleepAt'] == at(DAY, '23:40')
    assert body['wakeSource'] == body['sleepSource'] == 'manual'

    assert client.delete(f'/api/calendar/sleep/{DAY}').status_code == 200
    after = get_sleep(client)
    assert after['wakeAt'] is None and after['sleepAt'] is None


def test_an_omitted_end_hands_that_end_back_to_the_derived_value(client):
    journal(at(DAY, '09:00'))
    body = put_sleep(client, wake='07:20', sleep='23:40').get_json()
    assert body['wakeSource'] == 'manual'
    # The body is the whole manual state for the day, so a second write with
    # only `sleep` releases the wake time rather than leaving it stuck.
    body = put_sleep(client, sleep='23:40').get_json()
    assert (body['wakeAt'], body['wakeSource']) == (at(DAY, '09:00'), 'auto')


def test_the_payload_carries_the_neighbouring_ends_the_bands_need(client):
    # The night before this day ended at its wake time; the night after ends at
    # tomorrow's. Both live on other day keys, so the day view can't draw them
    # without being told.
    put_sleep(client, date='2026-07-07', sleep='23:10')
    put_sleep(client, date='2026-07-09', wake='06:45')
    body = get_sleep(client)
    assert body['previousSleepAt'] == at('2026-07-07', '23:10')
    assert body['nextWakeAt'] == at('2026-07-09', '06:45')


def test_a_bedtime_after_midnight_reads_back_as_the_same_wall_clock(client):
    body = put_sleep(client, sleep='01:30').get_json()
    assert body['sleepAt'] == at('2026-07-09', '01:30')
    assert datetime.fromtimestamp(body['sleepAt']).strftime('%H:%M') == '01:30'


def test_resolve_day_defaults_to_the_real_clock():
    # The only caller that passes `now` is a test; the routes rely on the
    # default, so a signature change there has to keep working.
    journal(int(time.time()) - 3600)
    today = day_key_for()
    assert sleep.resolve_day(today)['wakeAt'] is not None
