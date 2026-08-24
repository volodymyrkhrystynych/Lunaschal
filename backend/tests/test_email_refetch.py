"""Re-fetching message bodies stored before `emails.body_html` existed.

Nothing here talks to Gmail: `gmail_client` is monkeypatched, which is also the
point — the failure modes worth pinning are rate limits, deleted messages and
partial runs, none of which a live call would reproduce on demand.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.email import gmail_client, refetch

NOW = int(time.time())


@pytest.fixture
def account():
    db = get_db()
    account_id = str(ULID())
    db.execute(
        'INSERT INTO email_accounts (id, provider, email_address, access_token,'
        ' refresh_token, token_expires_at, created_at, updated_at)'
        " VALUES (?, 'gmail', 'me@example.com', 'at', 'rt', ?, ?, ?)",
        (account_id, NOW + 3600, NOW, NOW),
    )
    db.execute(
        'UPDATE settings SET google_oauth_client_id=?, google_oauth_client_secret=?',
        ('cid', 'secret'),
    )
    db.commit()
    return account_id


def add_email(account_id, *, provider_message_id, body_text='', body_html='',
              received_at=None, category='job_application'):
    db = get_db()
    email_id = str(ULID())
    db.execute(
        'INSERT INTO emails (id, account_id, provider_message_id, subject, sender,'
        ' sender_email, body_text, body_html, received_at, category, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (email_id, account_id, provider_message_id, 'Subject', 'S',
         's@example.com', body_text, body_html, received_at or NOW, category, NOW),
    )
    db.commit()
    return email_id


@pytest.fixture(autouse=True)
def no_token_refresh(monkeypatch):
    monkeypatch.setattr(gmail_client, 'get_valid_access_token',
                        lambda *a, **k: 'token')


def stub_messages(monkeypatch, payloads):
    """`payloads` maps provider_message_id -> the HTML that message has."""
    seen = []

    def get_message(token, message_id):
        seen.append(message_id)
        value = payloads.get(message_id)
        if isinstance(value, Exception):
            raise value
        return {'id': message_id}

    def parse_message(raw):
        return {'bodyHtml': payloads.get(raw['id']) or '', 'bodyText': 'refetched text'}

    monkeypatch.setattr(gmail_client, 'get_message', get_message)
    monkeypatch.setattr(gmail_client, 'parse_message', parse_message)
    return seen


# --- what gets picked up ------------------------------------------------------

def test_only_rows_with_no_html_are_candidates(account):
    db = get_db()
    add_email(account, provider_message_id='m1', body_html='')
    add_email(account, provider_message_id='m2', body_html='<p>already here</p>')

    ids = [c['provider_message_id'] for c in refetch.candidates(db)]
    assert ids == ['m1']
    assert refetch.count_missing(db) == 1


def test_candidates_can_be_narrowed_to_one_category(account):
    db = get_db()
    add_email(account, provider_message_id='m1', category='job_application')
    add_email(account, provider_message_id='m2', category='newsletter')

    ids = [c['provider_message_id'] for c in
           refetch.candidates(db, category='job_application')]
    assert ids == ['m1']
    assert refetch.count_missing(db, category='job_application') == 1


def test_newest_first(account):
    db = get_db()
    add_email(account, provider_message_id='old', received_at=NOW - 90000)
    add_email(account, provider_message_id='new', received_at=NOW)

    ids = [c['provider_message_id'] for c in refetch.candidates(db)]
    assert ids == ['new', 'old']


# --- filling ------------------------------------------------------------------

def test_a_refetch_fills_the_html_body(account, monkeypatch):
    db = get_db()
    email_id = add_email(account, provider_message_id='m1', body_text='plain only')
    stub_messages(monkeypatch, {'m1': '<p>The following items were sent to Pixel.</p>'})

    result = refetch.run(rate_per_second=0)
    assert result['filled'] == 1

    row = db.execute('SELECT body_html, body_text FROM emails WHERE id=?',
                     (email_id,)).fetchone()
    assert 'Pixel' in row['body_html']


def test_an_existing_text_body_is_never_replaced(account, monkeypatch):
    """`category` and `job_status` were derived from the stored text. Replacing
    it would silently invalidate a classification nobody re-ran."""
    db = get_db()
    email_id = add_email(account, provider_message_id='m1',
                         body_text='what the classifier read')
    stub_messages(monkeypatch, {'m1': '<p>html</p>'})

    refetch.run(rate_per_second=0)
    row = db.execute('SELECT body_text FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['body_text'] == 'what the classifier read'


def test_an_empty_text_body_is_filled_in(account, monkeypatch):
    db = get_db()
    email_id = add_email(account, provider_message_id='m1', body_text='')
    stub_messages(monkeypatch, {'m1': '<p>html</p>'})

    refetch.run(rate_per_second=0)
    row = db.execute('SELECT body_text FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['body_text'] == 'refetched text'


def test_a_message_with_no_html_part_is_left_alone(account, monkeypatch):
    """Storing '' would be indistinguishable from 'not fetched yet', so the row
    stays a candidate rather than being marked done with nothing."""
    db = get_db()
    add_email(account, provider_message_id='m1')
    stub_messages(monkeypatch, {'m1': ''})

    result = refetch.run(rate_per_second=0)
    assert result['filled'] == 0
    assert refetch.count_missing(db) == 1


# --- the things that go wrong over thousands of messages ----------------------

def test_a_deleted_message_is_counted_not_failed(account, monkeypatch):
    """Gmail auto-purges spam and trash; over a mailbox this size some messages
    are simply gone, which is routine rather than a run failure."""
    add_email(account, provider_message_id='m1')
    stub_messages(monkeypatch, {'m1': gmail_client.GmailApiError('gone', status_code=404, reason=None)})

    result = refetch.run(rate_per_second=0)
    assert result['gone'] == 1
    assert result['failed'] == 0
    assert result['finished'] is True


def test_one_bad_message_does_not_end_the_run(account, monkeypatch):
    add_email(account, provider_message_id='m1', received_at=NOW)
    add_email(account, provider_message_id='m2', received_at=NOW - 10)
    stub_messages(monkeypatch, {
        'm1': gmail_client.GmailApiError('boom', status_code=400, reason=None),
        'm2': '<p>fine</p>',
    })

    result = refetch.run(rate_per_second=0)
    assert result['failed'] == 1
    assert result['filled'] == 1
    assert result['done'] == 2


def test_a_rate_limit_is_waited_out_rather_than_failed(account, monkeypatch):
    """429 means later, not no. Without the retry a run against a real mailbox
    gives up partway through for a reason that resolves itself in a second."""
    add_email(account, provider_message_id='m1')
    monkeypatch.setattr(time, 'sleep', lambda s: None)

    calls = {'n': 0}

    def get_message(token, message_id):
        calls['n'] += 1
        if calls['n'] < 3:
            raise gmail_client.GmailApiError('slow down', status_code=429, reason=None)
        return {'id': message_id}

    monkeypatch.setattr(gmail_client, 'get_message', get_message)
    monkeypatch.setattr(gmail_client, 'parse_message',
                        lambda raw: {'bodyHtml': '<p>ok</p>', 'bodyText': 't'})

    result = refetch.run(rate_per_second=0)
    assert result['filled'] == 1
    assert calls['n'] == 3


def test_a_permanent_error_is_not_retried(account, monkeypatch):
    """Retrying a 400 is just a slower way to get the same answer."""
    add_email(account, provider_message_id='m1')
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    calls = {'n': 0}

    def get_message(token, message_id):
        calls['n'] += 1
        raise gmail_client.GmailApiError('nope', status_code=400, reason=None)

    monkeypatch.setattr(gmail_client, 'get_message', get_message)
    refetch.run(rate_per_second=0)
    assert calls['n'] == 1


# --- resumability and pacing --------------------------------------------------

def test_a_second_run_does_not_refetch_what_the_first_filled(account, monkeypatch):
    """Progress is the data itself, so there is no cursor to corrupt and an
    interrupted run simply continues."""
    add_email(account, provider_message_id='m1')
    seen = stub_messages(monkeypatch, {'m1': '<p>html</p>'})

    refetch.run(rate_per_second=0)
    refetch.run(rate_per_second=0)
    assert seen == ['m1']


def test_the_limit_bounds_one_run(account, monkeypatch):
    for i in range(5):
        add_email(account, provider_message_id=f'm{i}', received_at=NOW - i)
    seen = stub_messages(monkeypatch, {f'm{i}': '<p>h</p>' for i in range(5)})

    result = refetch.run(limit=2, rate_per_second=0)
    assert result['total'] == 2
    assert len(seen) == 2


def test_a_requested_rate_above_gmails_ceiling_is_clamped():
    """200/s draws 1,000 quota units a second against a 250 limit. Asking for
    it should yield a run that finishes at 50/s, not one that collects 429s."""
    assert refetch.GMAIL_MAX_PER_SECOND == 50.0
    throttle = refetch._Throttle(min(200.0, refetch.GMAIL_MAX_PER_SECOND))
    assert throttle.interval == pytest.approx(0.02)


def test_the_throttle_measures_from_the_last_call_not_a_flat_sleep():
    """A flat sleep per iteration adds the request's own latency on top, so a
    nominal 40/s halves against a real network."""
    throttle = refetch._Throttle(50.0)
    start = time.monotonic()
    for _ in range(3):
        throttle.wait()
    assert time.monotonic() - start < 0.5


def test_unconfigured_oauth_reports_rather_than_raising(account, monkeypatch):
    db = get_db()
    db.execute('UPDATE settings SET google_oauth_client_id=NULL')
    db.commit()

    result = refetch.run(rate_per_second=0)
    assert result['finished'] is True
    assert 'OAuth' in result['error']
