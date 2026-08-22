"""The Lifestyle tab's weekly trend chart: pure bucketing + the /trends route."""
from datetime import date, datetime, timedelta

from ulid import ULID

from backend.db.connection import get_db
from backend.lifestyle.trends import week_start, weekly_series


# --- Pure bucketing ---

def test_week_start_folds_a_whole_week_onto_its_monday():
    monday = date(2026, 8, 10)
    for offset in range(7):
        assert week_start(monday + timedelta(days=offset)) == monday
    # Sunday belongs to the week that started six days earlier, not the next one.
    assert week_start(date(2026, 8, 9)) == date(2026, 8, 3)


def test_weekly_series_emits_every_week_including_the_empty_ones():
    weeks = weekly_series({'2026-08-12': {'a': 3}}, date(2026, 8, 12), 3)
    assert [w['weekStart'] for w in weeks] == ['2026-07-27', '2026-08-03', '2026-08-10']
    assert [w['a'] for w in weeks] == [0, 0, 3]


def test_weekly_series_sums_the_days_inside_one_week():
    counts = {'2026-08-10': {'a': 1}, '2026-08-13': {'a': 2}, '2026-08-16': {'a': 4}}
    weeks = weekly_series(counts, date(2026, 8, 16), 1)
    assert weeks == [{'weekStart': '2026-08-10', 'a': 7}]


def test_weekly_series_zero_fills_a_series_missing_from_a_week():
    counts = {'2026-08-12': {'a': 1}, '2026-08-03': {'b': 2}}
    weeks = weekly_series(counts, date(2026, 8, 12), 2)
    assert weeks[0] == {'weekStart': '2026-08-03', 'a': 0, 'b': 2}
    assert weeks[1] == {'weekStart': '2026-08-10', 'a': 1, 'b': 0}


def test_weekly_series_drops_days_outside_the_window_rather_than_clamping():
    """Otherwise the first bucket quietly means "everything before", which reads
    as a spike the user never had."""
    counts = {'2026-01-01': {'a': 99}, '2026-08-12': {'a': 1}}
    weeks = weekly_series(counts, date(2026, 8, 12), 2)
    assert [w['a'] for w in weeks] == [0, 1]


def test_weekly_series_ignores_a_malformed_day_key():
    weeks = weekly_series({'not-a-date': {'a': 5}, '2026-08-12': {'a': 1}},
                          date(2026, 8, 12), 1)
    assert weeks == [{'weekStart': '2026-08-10', 'a': 1}]


def test_weekly_series_of_no_weeks_is_empty():
    assert weekly_series({'2026-08-12': {'a': 1}}, date(2026, 8, 12), 0) == []


# --- Route ---

def _local_ts(day: date, hour: int = 12) -> int:
    return int(datetime.combine(day, datetime.min.time()).timestamp()) + hour * 3600


def _insert_journal(day: date, hour: int = 12) -> None:
    ts = _local_ts(day, hour)
    db = get_db()
    db.execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at) VALUES (?,?,?,?)',
        (str(ULID()), 'entry', ts, ts),
    )
    db.commit()


def _account() -> str:
    db = get_db()
    row = db.execute('SELECT id FROM email_accounts LIMIT 1').fetchone()
    if row:
        return row['id']
    account_id = str(ULID())
    db.execute(
        "INSERT INTO email_accounts(id, provider, email_address, sync_enabled,"
        " created_at, updated_at) VALUES (?, 'gmail', 'me@example.com', 1, ?, ?)",
        (account_id, 0, 0),
    )
    db.commit()
    return account_id


def _insert_email(day: date, *, category='job_application', job_status='sent') -> None:
    db = get_db()
    row_id = str(ULID())
    db.execute(
        'INSERT INTO emails(id, account_id, provider_message_id, subject, sender_email,'
        ' received_at, category, job_status, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?)',
        (row_id, _account(), row_id, 'Application received', 'hr@example.com',
         _local_ts(day), category, job_status, _local_ts(day)),
    )
    db.commit()


def test_trends_counts_this_week_on_both_series(client):
    today = date.today()
    _insert_journal(today)
    _insert_journal(today)
    _insert_email(today)

    weeks = client.get('/api/lifestyle/trends?weeks=2').get_json()['weeks']
    assert len(weeks) == 2
    assert weeks[-1]['weekStart'] == week_start(today).isoformat()
    assert weeks[-1]['journalEntries'] == 2
    assert weeks[-1]['applications'] == 1


def test_trends_only_counts_applications_that_were_sent(client):
    today = date.today()
    _insert_email(today, job_status='rejection')
    _insert_email(today, job_status='interview_next_step')
    _insert_email(today, category='newsletter', job_status=None)
    _insert_email(today, job_status='sent')

    weeks = client.get('/api/lifestyle/trends').get_json()['weeks']
    assert weeks[-1]['applications'] == 1


def test_trends_returns_zero_filled_weeks_with_no_data_at_all(client):
    weeks = client.get('/api/lifestyle/trends?weeks=4').get_json()['weeks']
    assert len(weeks) == 4
    # Both series are named even when nothing was ever logged — a chart with a
    # missing key draws no line instead of a flat zero one.
    assert all(w['applications'] == 0 and w['journalEntries'] == 0 for w in weeks)


def test_trends_defaults_to_26_weeks_and_clamps_absurd_requests(client):
    assert len(client.get('/api/lifestyle/trends').get_json()['weeks']) == 26
    assert len(client.get('/api/lifestyle/trends?weeks=0').get_json()['weeks']) == 1
    assert len(client.get('/api/lifestyle/trends?weeks=9999').get_json()['weeks']) == 104
    # A non-numeric value falls back to the default rather than 400ing.
    assert len(client.get('/api/lifestyle/trends?weeks=lots').get_json()['weeks']) == 26


def test_trends_ignores_entries_older_than_the_window(client):
    today = date.today()
    _insert_journal(today - timedelta(weeks=6))
    _insert_journal(today)
    weeks = client.get('/api/lifestyle/trends?weeks=2').get_json()['weeks']
    assert sum(w['journalEntries'] for w in weeks) == 1
