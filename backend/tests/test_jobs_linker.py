"""The linkage sweep against a real database, including the scenario the whole
feature exists for: a rejection arrives from an ATS and finds its application.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.jobs import linker, scheduler

DAY = 86400
NOW = int(time.time())


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBS_ROOT', str(tmp_path / 'jobs'))


def make_account(db):
    account_id = str(ULID())
    db.execute(
        'INSERT INTO email_accounts (id, provider, email_address, created_at, updated_at)'
        " VALUES (?, 'gmail', 'me@example.com', ?, ?)",
        (account_id, NOW, NOW),
    )
    db.commit()
    return account_id


def make_email(db, account_id, *, subject, sender_email, job_status=None,
               received_at=None, body='', category='job_application'):
    email_id = str(ULID())
    db.execute(
        'INSERT INTO emails (id, account_id, gmail_id, subject, sender, sender_email,'
        ' body_text, received_at, category, job_status, created_at)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (email_id, account_id, str(ULID()), subject, sender_email, sender_email,
         body, received_at or NOW, category, job_status, NOW),
    )
    db.commit()
    return email_id


# Distinct from None, which is a meaningful value here ("never submitted").
_DEFAULT = object()


def make_application(db, *, company='Acme', title='Backend Engineer',
                     url='https://acme.com/jobs/1', applied_at=_DEFAULT,
                     status='submitted'):
    job_id, application_id = str(ULID()), str(ULID())
    db.execute(
        'INSERT INTO jobs (id, source, source_id, company, title, url, created_at, updated_at)'
        " VALUES (?, 'manual', ?, ?, ?, ?, ?, ?)",
        (job_id, job_id, company, title, url, NOW, NOW),
    )
    db.execute(
        'INSERT INTO applications (id, job_id, status, applied_at, created_at, updated_at)'
        ' VALUES (?, ?, ?, ?, ?, ?)',
        (application_id, job_id, status,
         NOW - 7 * DAY if applied_at is _DEFAULT else applied_at, NOW, NOW),
    )
    db.commit()
    return application_id


def status_of(db, application_id):
    return db.execute(
        'SELECT status FROM applications WHERE id=?', (application_id,)
    ).fetchone()['status']


# --- the headline scenario ------------------------------------------------

def test_ats_rejection_links_and_advances_status(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    application_id = make_application(db)
    make_email(db, account_id, subject='Your application to Acme',
               sender_email='no-reply@greenhouse.io', job_status='rejection')

    result = linker.run_linkage_sweep(now=NOW)
    assert result == {'scanned': 1, 'linked': 1}
    assert status_of(db, application_id) == 'rejected'

    link = db.execute('SELECT * FROM job_email_links').fetchone()
    assert link['application_id'] == application_id
    assert link['link_kind'] == 'auto'


def test_a_stale_confirmation_cannot_demote_a_rejection(client, jobs_root):
    """The ordering guarantee, exercised through the DB rather than in the unit."""
    db = get_db()
    account_id = make_account(db)
    application_id = make_application(db)

    make_email(db, account_id, subject='Your application to Acme',
               sender_email='no-reply@greenhouse.io', job_status='rejection',
               received_at=NOW)
    linker.run_linkage_sweep(now=NOW)
    assert status_of(db, application_id) == 'rejected'

    make_email(db, account_id, subject='We received your Acme application',
               sender_email='no-reply@greenhouse.io', job_status='sent',
               received_at=NOW + 60)
    linker.run_linkage_sweep(now=NOW + 60)
    assert status_of(db, application_id) == 'rejected'


def test_rejection_stamps_closed_at_and_a_purge_date(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    application_id = make_application(db)
    make_email(db, account_id, subject='Acme', sender_email='no-reply@greenhouse.io',
               job_status='rejection')

    linker.run_linkage_sweep(now=NOW)
    row = db.execute('SELECT closed_at, purge_after FROM applications WHERE id=?',
                     (application_id,)).fetchone()
    assert row['closed_at'] == NOW
    assert row['purge_after'] == NOW + 30 * DAY


# --- scanning bookkeeping -------------------------------------------------

def test_emails_are_only_scanned_once(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    make_application(db)
    make_email(db, account_id, subject='Acme', sender_email='no-reply@greenhouse.io')

    assert linker.run_linkage_sweep(now=NOW)['scanned'] == 1
    assert linker.run_linkage_sweep(now=NOW)['scanned'] == 0


def test_non_job_email_is_ignored(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    make_application(db)
    make_email(db, account_id, subject='Acme newsletter',
               sender_email='news@acme.com', category='newsletter')
    assert linker.run_linkage_sweep(now=NOW)['scanned'] == 0


def test_unmatched_email_is_recorded_and_surfaced_for_a_human(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    make_application(db, company='Acme')
    email_id = make_email(db, account_id, subject='Globex hiring update',
                          sender_email='no-reply@globex.example')

    assert linker.run_linkage_sweep(now=NOW) == {'scanned': 1, 'linked': 0}
    scan = db.execute('SELECT * FROM job_email_scans WHERE email_id=?', (email_id,)).fetchone()
    assert scan['matched'] == 0
    assert [e['id'] for e in linker.unlinked_job_emails(db)] == [email_id]


def test_a_new_application_reopens_the_no_match_verdicts(client, jobs_root):
    """The email arrived before the application was recorded — the commonest
    case for a confirmation, and useless if the verdict were permanent."""
    db = get_db()
    account_id = make_account(db)
    email_id = make_email(db, account_id, subject='Your application to Acme',
                          sender_email='no-reply@greenhouse.io', job_status='sent',
                          received_at=NOW)

    assert linker.run_linkage_sweep(now=NOW) == {'scanned': 1, 'linked': 0}

    application_id = make_application(db, company='Acme', applied_at=NOW)
    linker.rescan_since(db, NOW)
    assert linker.run_linkage_sweep(now=NOW) == {'scanned': 1, 'linked': 1}

    link = db.execute('SELECT * FROM job_email_links WHERE email_id=?', (email_id,)).fetchone()
    assert link['application_id'] == application_id
    assert status_of(db, application_id) == 'acknowledged'


def test_rescan_keeps_confirmed_matches(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    make_application(db)
    make_email(db, account_id, subject='Acme', sender_email='no-reply@greenhouse.io')
    linker.run_linkage_sweep(now=NOW)

    linker.rescan_since(db, NOW)
    assert db.execute('SELECT COUNT(*) c FROM job_email_scans').fetchone()['c'] == 1


def test_ambiguous_mail_is_suggested_not_linked(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    first = make_application(db, company='Acme', title='Backend Engineer',
                             url='https://boards.greenhouse.io/acme/1')
    make_application(db, company='Acme', title='Backend Engineer',
                     url='https://boards.greenhouse.io/acme/2')
    email_id = make_email(db, account_id, subject='Your Acme application',
                          sender_email='no-reply@greenhouse.io')

    assert linker.run_linkage_sweep(now=NOW)['linked'] == 0
    suggestions = linker.suggestions_for_email(db, email_id)
    assert len(suggestions) == 2
    assert first in [s['applicationId'] for s in suggestions]
    assert all(s['reasons'] for s in suggestions)


def test_draft_applications_are_never_matched(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    make_application(db, status='draft', applied_at=None)
    make_email(db, account_id, subject='Your application to Acme',
               sender_email='careers@acme.com')
    assert linker.run_linkage_sweep(now=NOW)['linked'] == 0


def test_manual_link_applies_the_status_change(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    application_id = make_application(db)
    email_id = make_email(db, account_id, subject='unrelated subject',
                          sender_email='someone@example.com',
                          job_status='interview_next_step')

    linker.link(db, application_id, email_id, 1.0, 'manual', now=NOW)
    assert linker.apply_email_status(db, application_id, 'interview_next_step', now=NOW) \
        == 'interview'
    assert status_of(db, application_id) == 'interview'


def test_link_is_idempotent(client, jobs_root):
    db = get_db()
    account_id = make_account(db)
    application_id = make_application(db)
    email_id = make_email(db, account_id, subject='Acme', sender_email='careers@acme.com')

    assert linker.link(db, application_id, email_id, 0.9, now=NOW) is True
    assert linker.link(db, application_id, email_id, 0.9, now=NOW) is False
    assert db.execute('SELECT COUNT(*) c FROM job_email_links').fetchone()['c'] == 1


# --- the scheduler --------------------------------------------------------

def test_tick_runs_linkage_every_pass(client, jobs_root):
    from datetime import datetime

    db = get_db()
    account_id = make_account(db)
    make_application(db)
    make_email(db, account_id, subject='Acme', sender_email='no-reply@greenhouse.io')

    results, _ = scheduler.tick(now=datetime(2026, 8, 15, 14, 0), last_purge_date=None)
    assert results['linkage'] == {'scanned': 1, 'linked': 1}
    # Outside the purge window, so no file deletion was even considered.
    assert results['purge'] is None


def test_tick_purges_once_a_day_inside_the_window(client, jobs_root):
    from datetime import datetime

    in_window = datetime(2026, 8, 15, 7, 30)
    results, last = scheduler.tick(now=in_window, last_purge_date=None)
    assert results['purge'] is not None
    assert last == in_window.date()

    results, _ = scheduler.tick(now=datetime(2026, 8, 15, 7, 45), last_purge_date=last)
    assert results['purge'] is None
