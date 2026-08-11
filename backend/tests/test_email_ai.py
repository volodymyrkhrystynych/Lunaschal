"""Unit tests for backend.ai.email.classify_email — confirms it calls the
shared chat_json helper with the right prompts and writes the result back to
the emails row, following the same monkeypatch style as test_recipes_ai.py."""
import time

from ulid import ULID

from backend.ai import email as email_ai
from backend.db.connection import get_db


def _insert_email(db, **overrides) -> str:
    row_id = str(ULID())
    account_id = str(ULID())
    now = int(time.time())
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at)"
        " VALUES (?, 'gmail', ?, 1, ?, ?)",
        (account_id, f'{account_id}@example.com', now, now),
    )
    defaults = dict(
        id=row_id, account_id=account_id, gmail_id='g1', thread_id='t1',
        subject='Your application to Acme', sender='Acme Recruiting', sender_email='r@acme.example',
        snippet='snip', body_text='Thanks for applying to the Acme role.', label_ids='[]',
        received_at=now, created_at=now,
    )
    defaults.update(overrides)
    db.execute(
        """
        INSERT INTO emails
            (id, account_id, gmail_id, thread_id, subject, sender, sender_email,
             snippet, body_text, label_ids, received_at, created_at)
        VALUES (:id, :account_id, :gmail_id, :thread_id, :subject, :sender, :sender_email,
                :snippet, :body_text, :label_ids, :received_at, :created_at)
        """,
        defaults,
    )
    db.commit()
    return row_id


def test_classify_email_non_job_category_skips_second_call(client, monkeypatch):
    db = get_db()
    email_id = _insert_email(db, subject='Weekly newsletter', body_text='Here is this week roundup')

    calls = []

    def fake_chat_json(text, system=None, schema=None):
        calls.append(system)
        return {'category': 'newsletter'}

    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(email_ai, 'chat_json', fake_chat_json)

    email_ai.classify_email(email_id)

    assert calls == [email_ai._CATEGORY_SYSTEM]
    row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['category'] == 'newsletter'
    assert row['job_status'] is None
    assert row['classified_at'] is not None
    assert row['classification_error'] is None


def test_classify_email_job_application_makes_second_call_for_status(client, monkeypatch):
    db = get_db()
    email_id = _insert_email(db)

    responses = iter([{'category': 'job_application'}, {'status': 'rejection'}])
    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(email_ai, 'chat_json', lambda text, system=None, schema=None: next(responses))

    email_ai.classify_email(email_id)

    row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['category'] == 'job_application'
    assert row['job_status'] == 'rejection'


def test_classify_email_out_of_vocabulary_category_falls_back_to_other(client, monkeypatch):
    db = get_db()
    email_id = _insert_email(db)

    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(email_ai, 'chat_json', lambda text, system=None, schema=None: {'category': 'nonsense'})

    email_ai.classify_email(email_id)

    row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['category'] == 'other'


def test_classify_email_unconfigured_leaves_row_pending(client, monkeypatch):
    db = get_db()
    email_id = _insert_email(db)
    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: False)

    email_ai.classify_email(email_id)

    row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['category'] is None
    assert row['classified_at'] is None


def test_classify_email_missing_row_is_a_noop(client):
    email_ai.classify_email('does-not-exist')  # must not raise


def test_classify_email_llm_failure_records_error_and_stays_pending(client, monkeypatch):
    db = get_db()
    email_id = _insert_email(db)
    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: True)

    def boom(text, system=None, schema=None):
        raise RuntimeError('llm unreachable')

    monkeypatch.setattr(email_ai, 'chat_json', boom)

    email_ai.classify_email(email_id)

    row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    assert row['classified_at'] is None
    assert row['classification_error'] == 'llm unreachable'


def test_sweep_unclassified_reenqueues_pending_rows(client, monkeypatch):
    db = get_db()
    pending_id = _insert_email(db)
    done_id = _insert_email(db, gmail_id='g2')
    db.execute(
        'UPDATE emails SET classified_at=?, category=? WHERE id=?',
        (int(time.time()), 'other', done_id),
    )
    db.commit()

    enqueued = []
    monkeypatch.setattr('backend.ai.background.run_bg', lambda fn: enqueued.append(fn))

    count = email_ai.sweep_unclassified()

    assert count == 1
    assert len(enqueued) == 1
    assert done_id  # sanity: the classified row exists and wasn't touched
