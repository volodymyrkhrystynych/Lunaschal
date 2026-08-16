"""Retention: the date policy, and the purge that keeps the record but not the
files."""
import json
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.jobs import retention, storage

DAY = 86400
NOW = int(time.time())


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBS_ROOT', str(tmp_path / 'jobs'))
    return tmp_path / 'jobs'


def make_application(db, *, applied_at=None, status='submitted', closed_at=None):
    job_id, application_id = str(ULID()), str(ULID())
    db.execute(
        'INSERT INTO jobs (id, source, source_id, company, title, created_at, updated_at)'
        " VALUES (?, 'manual', ?, 'Acme', 'Backend Engineer', ?, ?)",
        (job_id, job_id, NOW, NOW),
    )
    db.execute(
        'INSERT INTO applications (id, job_id, status, applied_at, closed_at,'
        ' created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (application_id, job_id, status, applied_at, closed_at, NOW, NOW),
    )
    db.commit()
    return application_id


def make_resume(db, application_id, *, with_files=True):
    version_id = str(ULID())
    pdf = storage.resume_path(application_id, version_id, 'pdf')
    docx = storage.resume_path(application_id, version_id, 'docx')
    if with_files:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b'%PDF-1.4 fake')
        docx.write_bytes(b'PK fake')
    db.execute(
        'INSERT INTO resume_versions (id, application_id, content, html, pdf_path,'
        ' docx_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (version_id, application_id, json.dumps({'summary': 'kept forever'}),
         '<div>resume</div>', str(pdf), str(docx), NOW),
    )
    db.commit()
    return version_id, pdf, docx


# --- the pure policy ------------------------------------------------------

def test_six_month_clock_runs_from_applied_at():
    policy = retention.RetentionPolicy(retention_days=180)
    applied = NOW - 179 * DAY
    assert not retention.due_for_purge(applied, 'submitted', None, policy, NOW)
    assert retention.due_for_purge(applied - 2 * DAY, 'submitted', None, policy, NOW)


def test_rejection_purges_sooner_than_the_six_month_clock():
    policy = retention.RetentionPolicy(retention_days=180, rejection_grace_days=30)
    applied, closed = NOW - 60 * DAY, NOW - 31 * DAY
    assert retention.due_for_purge(applied, 'rejected', closed, policy, NOW)


def test_rejection_grace_period_is_respected():
    policy = retention.RetentionPolicy(retention_days=180, rejection_grace_days=30)
    applied, closed = NOW - 60 * DAY, NOW - 5 * DAY
    assert not retention.due_for_purge(applied, 'rejected', closed, policy, NOW)


def test_rejection_purge_can_be_turned_off():
    policy = retention.RetentionPolicy(retention_days=180, purge_on_rejection=False)
    applied, closed = NOW - 60 * DAY, NOW - 90 * DAY
    assert not retention.due_for_purge(applied, 'rejected', closed, policy, NOW)


def test_an_offer_never_takes_the_short_clock():
    """The one outcome where the paperwork matters most."""
    policy = retention.RetentionPolicy(retention_days=180, rejection_grace_days=30)
    assert not retention.due_for_purge(NOW - 60 * DAY, 'offer', NOW - 60 * DAY, policy, NOW)


def test_never_submitted_has_no_purge_date():
    policy = retention.RetentionPolicy()
    assert retention.purge_due_at(None, 'draft', None, policy) is None
    assert not retention.due_for_purge(None, 'draft', None, policy, NOW)


def test_whichever_clock_comes_first_wins():
    policy = retention.RetentionPolicy(retention_days=180, rejection_grace_days=30)

    # Rejected early: the grace clock is the earlier of the two.
    applied, closed = NOW - 20 * DAY, NOW - 1 * DAY
    assert retention.purge_due_at(applied, 'rejected', closed, policy) == closed + 30 * DAY

    # Rejected long after applying: the six-month clock already ran down first.
    applied, closed = NOW - 170 * DAY, NOW - 1 * DAY
    assert retention.purge_due_at(applied, 'rejected', closed, policy) == applied + 180 * DAY


def test_policy_reads_settings_and_falls_back(client):
    row = get_db().execute('SELECT * FROM settings WHERE id=1').fetchone()
    policy = retention.RetentionPolicy.from_settings(row)
    assert policy.retention_days == 180
    assert policy.purge_on_rejection is True
    assert retention.RetentionPolicy.from_settings(None).retention_days == 180


# --- the executor ---------------------------------------------------------

def test_purge_deletes_files_but_keeps_the_record(client, jobs_root):
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 200 * DAY)
    version_id, pdf, docx = make_resume(db, application_id)
    assert pdf.is_file() and docx.is_file()

    result = retention.run_purge_sweep(now=NOW)
    assert result == {'applications': 1, 'files': 2}

    assert not pdf.exists()
    assert not docx.exists()

    row = db.execute('SELECT * FROM resume_versions WHERE id=?', (version_id,)).fetchone()
    assert row['purged_at'] is not None
    assert row['pdf_path'] is None and row['docx_path'] is None
    # The point of the whole design: what you sent is still answerable.
    assert json.loads(row['content'])['summary'] == 'kept forever'
    assert row['html'] == '<div>resume</div>'


def test_purge_removes_the_empty_directory(client, jobs_root):
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 200 * DAY)
    make_resume(db, application_id)
    retention.run_purge_sweep(now=NOW)
    assert not (jobs_root / application_id).exists()


def test_purge_skips_applications_that_are_not_due(client, jobs_root):
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 10 * DAY)
    _, pdf, _ = make_resume(db, application_id)
    assert retention.run_purge_sweep(now=NOW) == {'applications': 0, 'files': 0}
    assert pdf.is_file()


def test_purge_is_idempotent(client, jobs_root):
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 200 * DAY)
    make_resume(db, application_id)
    assert retention.run_purge_sweep(now=NOW)['applications'] == 1
    assert retention.run_purge_sweep(now=NOW) == {'applications': 0, 'files': 0}


def test_purge_refuses_a_path_outside_the_jobs_root(client, jobs_root, tmp_path):
    """Defence in depth: a tampered pdf_path must not delete an arbitrary file."""
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 200 * DAY)
    outsider = tmp_path / 'precious.txt'
    outsider.write_text('do not delete me')
    db.execute(
        'INSERT INTO resume_versions (id, application_id, content, pdf_path, created_at)'
        ' VALUES (?, ?, ?, ?, ?)',
        (str(ULID()), application_id, '{}', str(outsider), NOW),
    )
    db.commit()

    retention.run_purge_sweep(now=NOW)
    assert outsider.is_file()


# --- closed_at bookkeeping ------------------------------------------------

def test_stamp_closed_uses_the_status_it_is_given_not_the_stored_one(client, jobs_root):
    """The caller's status wins, so callers cannot get the date wrong by
    stamping before they write."""
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 10 * DAY, status='submitted')
    retention.stamp_closed(db, application_id, 'rejected', now=NOW)
    row = db.execute('SELECT purge_after FROM applications WHERE id=?',
                     (application_id,)).fetchone()
    assert row['purge_after'] == NOW + 30 * DAY


def test_stamp_closed_sets_and_clears(client, jobs_root):
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 10 * DAY)

    retention.stamp_closed(db, application_id, 'rejected', now=NOW)
    row = db.execute('SELECT closed_at, purge_after FROM applications WHERE id=?',
                     (application_id,)).fetchone()
    assert row['closed_at'] == NOW
    assert row['purge_after'] == NOW + 30 * DAY

    retention.stamp_closed(db, application_id, 'interview', now=NOW + DAY)
    row = db.execute('SELECT closed_at FROM applications WHERE id=?',
                     (application_id,)).fetchone()
    assert row['closed_at'] is None


def test_stamp_closed_does_not_restart_the_clock(client, jobs_root):
    """rejected -> ghosted must not buy another grace period."""
    db = get_db()
    application_id = make_application(db, applied_at=NOW - 10 * DAY)
    retention.stamp_closed(db, application_id, 'rejected', now=NOW)
    retention.stamp_closed(db, application_id, 'ghosted', now=NOW + 20 * DAY)
    row = db.execute('SELECT closed_at FROM applications WHERE id=?',
                     (application_id,)).fetchone()
    assert row['closed_at'] == NOW
