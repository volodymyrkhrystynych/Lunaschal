"""The Archive: week-old expiry, dismissal, closed outcomes, search window."""
import time

from backend.db.connection import get_db
from backend.jobs import archive, linkage, scheduler, status as application_status

DAY = archive.DAY


def _job(client, title='Backend Engineer', company='Globex', description='Python.'):
    return client.post('/api/jobs', json={
        'title': title, 'company': company, 'description': description,
    }).get_json()['id']


def _age(job_id, days, now=None):
    now = int(time.time()) if now is None else now
    db = get_db()
    db.execute('UPDATE jobs SET created_at=? WHERE id=?', (now - days * DAY, job_id))
    db.commit()


def _feed_ids(client):
    return [j['id'] for j in client.get('/api/jobs/feed').get_json()]


def _ready_application(client, job_id, ready_days_ago):
    app_id = client.post('/api/jobs/applications', json={'jobId': job_id}).get_json()['id']
    db = get_db()
    application_status.record(db, app_id, 'ready',
                              at=int(time.time()) - ready_days_ago * DAY)
    db.commit()
    return app_id


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------

def test_a_posting_untouched_for_a_week_leaves_the_feed_for_the_archive(client):
    old, fresh = _job(client, 'Old'), _job(client, 'Fresh')
    _age(old, 8)
    _age(fresh, 6)

    assert archive.expire_stale(get_db()) == {'postings': 1, 'ready': 0}

    assert _feed_ids(client) == [fresh]
    rows = client.get('/api/jobs/archive').get_json()
    assert [(r['jobId'], r['reason']) for r in rows] == [(old, 'expired')]


def test_the_sweep_is_idempotent(client):
    _age(_job(client), 8)
    archive.expire_stale(get_db())
    assert archive.expire_stale(get_db()) == {'postings': 0, 'ready': 0}


def test_triage_rejected_postings_are_not_archived(client):
    """They were never in front of the user and keep their own audit list."""
    job_id = _job(client)
    _age(job_id, 30)
    db = get_db()
    db.execute("UPDATE jobs SET triage_state='rejected' WHERE id=?", (job_id,))
    db.commit()
    archive.expire_stale(db)
    assert client.get('/api/jobs/archive').get_json() == []


def test_a_ready_resume_unsent_for_a_week_is_archived(client):
    stale_job, fresh_job = _job(client, 'Stale'), _job(client, 'Fresh')
    stale_app = _ready_application(client, stale_job, 8)
    fresh_app = _ready_application(client, fresh_job, 2)

    assert archive.expire_stale(get_db()) == {'postings': 0, 'ready': 1}

    apps = {a['id']: a for a in client.get('/api/jobs/applications').get_json()}
    assert apps[stale_app]['archived'] is True
    assert apps[fresh_app]['archived'] is False
    assert apps[stale_app]['readyAt'] is not None


def test_sending_an_archived_application_brings_it_back(client):
    """Membership for an application is 'archived while unsent', so marking it
    submitted from the Archive needs nothing cleared."""
    job_id = _job(client)
    app_id = _ready_application(client, job_id, 8)
    archive.expire_stale(get_db())

    client.patch(f'/api/jobs/applications/{app_id}', json={'status': 'submitted'})

    apps = {a['id']: a for a in client.get('/api/jobs/applications').get_json()}
    assert apps[app_id]['archived'] is False
    assert client.get('/api/jobs/archive').get_json() == []


def test_the_scheduler_tick_runs_the_sweep_even_while_paused(client, monkeypatch):
    _age(_job(client), 8)
    db = get_db()
    db.execute('UPDATE settings SET jobs_paused=1')
    db.commit()
    monkeypatch.setattr(scheduler.linker, 'run_linkage_sweep', lambda: None)
    results, _ = scheduler.tick(last_purge_date=None)
    assert results['paused'] is True
    assert results['archived'] == {'postings': 1, 'ready': 0}


def test_an_archived_posting_is_not_sent_to_triage(client):
    from backend.jobs import triager
    job_id = _job(client)
    assert triager.pending_count(get_db()) == 1
    _age(job_id, 8)
    archive.expire_stale(get_db())
    assert triager.pending_count(get_db()) == 0


# --------------------------------------------------------------------------
# Dismissal and restore
# --------------------------------------------------------------------------

def test_dismissing_files_the_posting_under_dismissed_with_its_own_date(client):
    job_id = _job(client)
    before = int(time.time())
    assert client.post(f'/api/jobs/{job_id}/dismiss').status_code == 200

    rows = client.get('/api/jobs/archive').get_json()
    assert [(r['jobId'], r['reason']) for r in rows] == [(job_id, 'dismissed')]
    stamped = get_db().execute('SELECT archived_at FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
    assert stamped >= before


def test_dismissing_an_unknown_posting_is_a_404(client):
    assert client.post('/api/jobs/nope/dismiss').status_code == 404


def test_restore_gives_a_posting_a_fresh_week(client):
    job_id = _job(client)
    _age(job_id, 8)
    archive.expire_stale(get_db())

    assert client.post(f'/api/jobs/{job_id}/archive/restore').status_code == 200
    assert _feed_ids(client) == [job_id]
    # Still eight days old by created_at; the restore is what the clock reads.
    archive.expire_stale(get_db())
    assert _feed_ids(client) == [job_id]
    archive.expire_stale(get_db(), now=int(time.time()) + 8 * DAY)
    assert _feed_ids(client) == []


def test_restore_undoes_a_dismissal(client):
    job_id = _job(client)
    client.post(f'/api/jobs/{job_id}/dismiss')
    assert client.post(f'/api/jobs/{job_id}/archive/restore').status_code == 200
    assert _feed_ids(client) == [job_id]
    assert client.get('/api/jobs/archive').get_json() == []


def test_undismissing_also_restarts_the_clock(client):
    job_id = _job(client)
    _age(job_id, 8)
    client.post(f'/api/jobs/{job_id}/dismiss')
    client.post(f'/api/jobs/{job_id}/dismiss', json={'dismissed': False})
    archive.expire_stale(get_db())
    assert _feed_ids(client) == [job_id]


def test_restore_refuses_a_closed_application_and_an_unarchived_posting(client):
    job_id = _job(client)
    assert client.post(f'/api/jobs/{job_id}/archive/restore').status_code == 409
    app_id = client.post('/api/jobs/applications', json={'jobId': job_id}).get_json()['id']
    client.patch(f'/api/jobs/applications/{app_id}', json={'status': 'rejected'})
    assert client.post(f'/api/jobs/{job_id}/archive/restore').status_code == 409
    assert client.post('/api/jobs/nope/archive/restore').status_code == 404


# --------------------------------------------------------------------------
# Closed applications
# --------------------------------------------------------------------------

def test_closed_applications_are_archived_and_flagged_for_the_pipeline(client):
    job_id = _job(client)
    app_id = client.post('/api/jobs/applications', json={'jobId': job_id}).get_json()['id']
    client.patch(f'/api/jobs/applications/{app_id}', json={'status': 'ghosted'})

    rows = client.get('/api/jobs/archive').get_json()
    assert [(r['applicationId'], r['reason'], r['status']) for r in rows] == [
        (app_id, 'closed', 'ghosted')
    ]
    apps = client.get('/api/jobs/applications').get_json()
    assert apps[0]['archived'] is True


def test_a_reply_to_a_ghosted_application_brings_it_out_of_the_archive(client):
    """The auto-unarchive: membership follows the status, so the linker
    moving it on is all it takes."""
    assert linkage.advance_status('ghosted', 'other_update') == 'acknowledged'
    assert linkage.advance_status('submitted', 'other_update') is None
    assert linkage.advance_status('rejected', 'other_update') is None

    from backend.jobs import linker
    job_id = _job(client)
    app_id = client.post('/api/jobs/applications', json={'jobId': job_id}).get_json()['id']
    client.patch(f'/api/jobs/applications/{app_id}', json={'status': 'ghosted'})
    assert len(client.get('/api/jobs/archive').get_json()) == 1

    assert linker.apply_email_status(get_db(), app_id, 'other_update') == 'acknowledged'
    assert client.get('/api/jobs/archive').get_json() == []
    apps = client.get('/api/jobs/applications').get_json()
    assert apps[0]['archived'] is False


# --------------------------------------------------------------------------
# Listing and search
# --------------------------------------------------------------------------

def test_the_archive_is_newest_first(client):
    now = int(time.time())
    ids = [_job(client, t) for t in ('A', 'B', 'C')]
    db = get_db()
    for days, job_id in zip((20, 5, 10), ids):
        db.execute('UPDATE jobs SET archived_at=? WHERE id=?', (now - days * DAY, job_id))
    db.commit()
    assert [r['title'] for r in client.get('/api/jobs/archive').get_json()] == ['B', 'C', 'A']


def test_the_archive_never_reaches_back_past_half_a_year(client):
    now = int(time.time())
    recent, ancient = _job(client, 'Recent'), _job(client, 'Ancient')
    db = get_db()
    db.execute('UPDATE jobs SET archived_at=? WHERE id=?', (now - 170 * DAY, recent))
    db.execute('UPDATE jobs SET archived_at=? WHERE id=?', (now - 190 * DAY, ancient))
    db.commit()
    assert [r['title'] for r in client.get('/api/jobs/archive').get_json()] == ['Recent']
    assert client.get('/api/jobs/archive?q=Ancient').get_json() == []


def test_search_matches_every_word_across_fields_in_any_case(client):
    a = _job(client, 'Backend Engineer', 'Globex', 'Kafka and Postgres.')
    b = _job(client, 'Frontend Engineer', 'Initech', 'React.')
    for job_id in (a, b):
        client.post(f'/api/jobs/{job_id}/dismiss')

    def titles(q):
        return [r['title'] for r in client.get(f'/api/jobs/archive?q={q}').get_json()]

    assert sorted(titles('engineer')) == ['Backend Engineer', 'Frontend Engineer']
    assert titles('globex kafka') == ['Backend Engineer']
    assert titles('GLOBEX react') == []


def test_search_folds_case_beyond_ascii():
    assert archive.matches(['Розробник Python'], 'розробник')


def test_reason_filter_and_bad_params(client):
    a, b = _job(client, 'Expired'), _job(client, 'Dismissed')
    _age(a, 8)
    archive.expire_stale(get_db())
    client.post(f'/api/jobs/{b}/dismiss')
    rows = client.get('/api/jobs/archive?reason=expired').get_json()
    assert [r['title'] for r in rows] == ['Expired']
    assert client.get('/api/jobs/archive?reason=nope').status_code == 400
    assert client.get('/api/jobs/archive?limit=x').status_code == 400


def test_migration_backfills_dismissed_rows_from_created_at():
    import sqlite3
    from backend.db.connection import _ensure_job_archive_columns
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE jobs (id TEXT, dismissed INTEGER, created_at INTEGER)')
    db.execute("INSERT INTO jobs VALUES ('a', 1, 100), ('b', 0, 200)")
    _ensure_job_archive_columns(db)
    _ensure_job_archive_columns(db)
    rows = dict(db.execute('SELECT id, archived_at FROM jobs').fetchall())
    assert rows == {'a': 100, 'b': None}
