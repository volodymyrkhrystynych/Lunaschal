"""Tests for the non-OAuth email routes: list/get/stats/sync. Seeded
directly against a tmp DB via the `client` fixture, matching
test_newspapers.py's style."""
import time

from ulid import ULID

from backend.db.connection import get_db


def _insert_account(db) -> str:
    account_id = str(ULID())
    now = int(time.time())
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at)"
        " VALUES (?, 'gmail', 'me@example.com', 1, ?, ?)",
        (account_id, now, now),
    )
    db.commit()
    return account_id


def _insert_email(db, account_id, **overrides) -> str:
    row_id = str(ULID())
    now = int(time.time())
    defaults = dict(
        id=row_id, account_id=account_id, provider_message_id=row_id, thread_id='t',
        subject='Hello', sender='Someone', sender_email='s@example.com',
        snippet='snip', body_text='body text here', label_ids='[]',
        received_at=now, category=None, job_status=None, created_at=now,
    )
    defaults.update(overrides)
    db.execute(
        """
        INSERT INTO emails
            (id, account_id, provider_message_id, thread_id, subject, sender, sender_email,
             snippet, body_text, label_ids, received_at, category, job_status, created_at)
        VALUES (:id, :account_id, :provider_message_id, :thread_id, :subject, :sender, :sender_email,
                :snippet, :body_text, :label_ids, :received_at, :category, :job_status, :created_at)
        """,
        defaults,
    )
    db.commit()
    return row_id


def test_list_emails_empty(client):
    resp = client.get('/api/email')
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_emails_ordered_newest_first(client):
    db = get_db()
    account_id = _insert_account(db)
    older = _insert_email(db, account_id, received_at=1000)
    newer = _insert_email(db, account_id, received_at=2000)

    resp = client.get('/api/email')
    ids = [e['id'] for e in resp.get_json()]
    assert ids == [newer, older]


def test_list_emails_filters_by_category(client):
    db = get_db()
    account_id = _insert_account(db)
    _insert_email(db, account_id, category='newsletter')
    job_id = _insert_email(db, account_id, category='job_application')

    resp = client.get('/api/email?category=job_application')
    results = resp.get_json()
    assert len(results) == 1
    assert results[0]['id'] == job_id


def test_list_emails_filters_by_job_status(client):
    db = get_db()
    account_id = _insert_account(db)
    _insert_email(db, account_id, category='job_application', job_status='sent')
    rejected_id = _insert_email(db, account_id, category='job_application', job_status='rejection')

    resp = client.get('/api/email?jobStatus=rejection')
    results = resp.get_json()
    assert len(results) == 1
    assert results[0]['id'] == rejected_id


def test_get_email_by_id(client):
    db = get_db()
    account_id = _insert_account(db)
    email_id = _insert_email(db, account_id, subject='Specific subject')

    resp = client.get(f'/api/email/{email_id}')
    assert resp.status_code == 200
    assert resp.get_json()['subject'] == 'Specific subject'


def test_get_email_404(client):
    resp = client.get('/api/email/does-not-exist')
    assert resp.status_code == 404


def test_stats_counts_and_next_steps(client):
    db = get_db()
    account_id = _insert_account(db)
    _insert_email(db, account_id, category='job_application', job_status='sent')
    _insert_email(db, account_id, category='job_application', job_status='sent')
    _insert_email(db, account_id, category='job_application', job_status='rejection')
    next_step_id = _insert_email(
        db, account_id, category='job_application', job_status='interview_next_step', received_at=99999,
    )
    _insert_email(db, account_id, category='newsletter')

    resp = client.get('/api/email/stats')
    body = resp.get_json()
    assert body['sentCount'] == 2
    assert body['rejectionCount'] == 1
    assert body['interviewNextStepCount'] == 1
    assert body['otherUpdateCount'] == 0
    assert [s['id'] for s in body['nextSteps']] == [next_step_id]


def test_sync_now_without_account_is_400(client):
    resp = client.post('/api/email/sync')
    assert resp.status_code == 400


def test_sync_now_calls_sync_account(client, monkeypatch):
    db = get_db()
    account_id = _insert_account(db)

    monkeypatch.setattr(
        'backend.email.sync.sync_account', lambda account: {'status': 'ok', 'newCount': 3}
    )

    resp = client.post('/api/email/sync')
    assert resp.status_code == 200
    assert resp.get_json() == {account_id: {'status': 'ok', 'newCount': 3}}


def test_sync_now_with_provider_syncs_only_that_account(client, monkeypatch):
    db = get_db()
    _insert_account(db)

    monkeypatch.setattr(
        'backend.email.sync.sync_account', lambda account: {'status': 'ok', 'newCount': 5}
    )

    resp = client.post('/api/email/sync?provider=gmail')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'ok', 'newCount': 5}


def test_sync_now_with_unconnected_provider_is_400(client, monkeypatch):
    db = get_db()
    _insert_account(db)
    monkeypatch.setattr(
        'backend.email.sync.sync_account', lambda account: {'status': 'ok', 'newCount': 1}
    )

    resp = client.post('/api/email/sync?provider=outlook')
    assert resp.status_code == 400
