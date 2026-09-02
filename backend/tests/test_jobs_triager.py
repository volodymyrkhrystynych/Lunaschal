"""Triage against the database: state transitions, the gate sweep, the worker.

The model is stubbed throughout — what is under test is the discipline around
it. The rules that matter most are the ones that protect the user's own
decisions: a rejection must be auditable, a re-sync must not re-judge a posting
whose text never changed, and a posting the model chokes on must not block
everything behind it.
"""
import json
import time

import pytest
from ulid import ULID

from backend.ai import priority
from backend.db.connection import get_db
from backend.jobs import sync, triager

NOW = int(time.time())


@pytest.fixture(autouse=True)
def clean_worker(client):
    """Drain the worker before the DB goes away.

    Depending on `client` is load-bearing: finalizers run in reverse setup
    order, so without it this can tear down *after* conftest closes the
    connection, and a worker thread mid-query against a closed sqlite handle
    segfaults rather than raising. Same reasoning as test_jobs_queue.
    """
    triager.reset()
    yield
    triager.wait_idle(timeout=5)
    triager.reset()


@pytest.fixture(autouse=True)
def quiet_priority():
    priority._marks.clear()
    priority._released_at = time.monotonic() - 3600
    yield


def make_job(db, *, title='Backend Engineer', description='We use Python.',
             source='greenhouse', state='pending', created=NOW, url=''):
    job_id = str(ULID())
    db.execute(
        'INSERT INTO jobs (id, source, source_id, url, company, title,'
        ' description, triage_state, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
        (job_id, source, job_id, url, 'Acme', title, description, state,
         created, created),
    )
    db.commit()
    return job_id


def state_of(db, job_id):
    return db.execute(
        'SELECT * FROM jobs WHERE id=?', (job_id,)
    ).fetchone()


# --------------------------------------------------------------------------
# The free gate sweep
# --------------------------------------------------------------------------

def test_gate_sweep_rejects_on_title_without_a_model(client):
    db = get_db()
    cook = make_job(db, title='Line Cook')
    engineer = make_job(db, title='Backend Engineer')

    result = triager.run_gate_sweep(db)

    assert result['rejected'] == 1
    assert state_of(db, cook)['triage_state'] == 'rejected'
    assert state_of(db, engineer)['triage_state'] == 'pending'


def test_gate_records_why_it_rejected(client):
    db = get_db()
    job_id = make_job(db, title='Senior Paid Social Strategist')
    triager.run_gate_sweep(db)
    assert state_of(db, job_id)['triage_reason'] == 'title: paid social'


def test_gate_sweep_leaves_already_judged_rows_alone(client):
    """A posting a human restored must not be re-rejected on the next tick."""
    db = get_db()
    job_id = make_job(db, title='Line Cook', state='kept')
    triager.run_gate_sweep(db)
    assert state_of(db, job_id)['triage_state'] == 'kept'


def test_gate_sweep_skips_dismissed(client):
    db = get_db()
    job_id = make_job(db, title='Line Cook')
    db.execute('UPDATE jobs SET dismissed=1 WHERE id=?', (job_id,))
    db.commit()
    assert triager.run_gate_sweep(db)['scanned'] == 0


def test_gate_sweep_is_off_when_triage_is_disabled(client):
    db = get_db()
    db.execute('UPDATE settings SET job_triage_enabled=0')
    db.commit()
    make_job(db, title='Line Cook')
    assert triager.run_gate_sweep(db)['scanned'] == 0


def test_gate_sweep_reaches_rows_behind_a_batch_of_keepers(client, monkeypatch):
    """The batch bounds memory, not how far the sweep gets.

    A rejected row leaves `pending` and so leaves the result set, but a kept
    one stays in it forever. With a plain `LIMIT` the sweep re-reads the same
    leading keepers every tick and creeps forward only by what it just
    rejected — and once a full batch of them accumulates at the front it stops
    advancing entirely. Everything behind is then reachable only by the model
    drain, one generation at a time, which is how thousands of postings ended
    up queued behind a gate that had already decided about them.
    """
    monkeypatch.setattr(triager, 'GATE_BATCH', 2)
    db = get_db()
    keepers = [make_job(db, title='Backend Engineer') for _ in range(4)]
    cook = make_job(db, title='Line Cook')

    result = triager.run_gate_sweep(db)

    assert state_of(db, cook)['triage_state'] == 'rejected'
    assert result['rejected'] == 1
    assert result['scanned'] == 5
    for job_id in keepers:
        assert state_of(db, job_id)['triage_state'] == 'pending'


def test_gate_sweep_terminates_when_every_row_is_a_keeper(client, monkeypatch):
    """The paging cursor counts kept rows, so an all-keep table still ends."""
    monkeypatch.setattr(triager, 'GATE_BATCH', 2)
    db = get_db()
    for _ in range(5):
        make_job(db, title='Backend Engineer')

    result = triager.run_gate_sweep(db)

    assert result == {'scanned': 5, 'rejected': 0}


# --------------------------------------------------------------------------
# The model layer
# --------------------------------------------------------------------------

def test_a_relevant_posting_is_kept_with_its_summary(client, monkeypatch):
    db = get_db()
    job_id = make_job(db)
    monkeypatch.setattr(
        'backend.ai.job_triage.triage_posting',
        lambda *a, **k: {
            'relevant': True, 'reason': '', 'fit': 'strong',
            'summary': 'Builds a Python API.',
            'flags': [{'kind': 'onsite_required', 'detail': 'Toronto, 5 days.'}],
            'missingMustHaves': [],
        },
    )

    result = triager.process_one(job_id)

    assert result['ok'] and result['state'] == 'kept'
    row = state_of(db, job_id)
    assert row['triage_state'] == 'kept'
    assert row['triage_fit'] == 'strong'
    assert row['triage_summary'] == 'Builds a Python API.'
    assert json.loads(row['triage_flags'])[0]['kind'] == 'onsite_required'
    assert row['triage_at'] is not None


def test_an_irrelevant_posting_is_rejected_with_a_reason(client, monkeypatch):
    db = get_db()
    job_id = make_job(db, title='Revenue Operations Lead')
    monkeypatch.setattr(
        'backend.ai.job_triage.triage_posting',
        lambda *a, **k: {
            'relevant': False, 'reason': 'marketing ops, not engineering',
            'fit': 'stretch', 'summary': '', 'flags': [], 'missingMustHaves': [],
        },
    )

    assert triager.process_one(job_id)['state'] == 'rejected'
    row = state_of(db, job_id)
    assert row['triage_reason'] == 'marketing ops, not engineering'
    # A fit level computed for a posting that will not be shown is noise.
    assert row['triage_fit'] == ''


def test_the_gate_still_applies_on_a_forced_run(client, monkeypatch):
    """A direct call must not be a way around the cheap layer."""
    called = []
    monkeypatch.setattr(
        'backend.ai.job_triage.triage_posting',
        lambda *a, **k: called.append(1) or {'relevant': True},
    )
    db = get_db()
    job_id = make_job(db, title='Registered Nurse')

    assert triager.process_one(job_id)['state'] == 'rejected'
    assert called == []


def test_model_unavailable_leaves_the_row_pending(client, monkeypatch):
    """A verdict nobody reached must not be recorded as one that was."""
    db = get_db()
    job_id = make_job(db)
    monkeypatch.setattr('backend.ai.job_triage.triage_posting', lambda *a, **k: None)

    result = triager.process_one(job_id)

    assert result['ok'] is False
    row = state_of(db, job_id)
    assert row['triage_state'] == 'pending'
    assert row['triage_error'] is None


def test_a_failure_is_recorded_not_swallowed(client, monkeypatch):
    db = get_db()
    job_id = make_job(db)

    def boom(*a, **k):
        raise RuntimeError('grammar rejected')

    monkeypatch.setattr('backend.ai.job_triage.triage_posting', boom)

    result = triager.process_one(job_id)

    assert result['ok'] is False
    assert 'grammar rejected' in state_of(db, job_id)['triage_error']


def test_a_failed_posting_is_skipped_not_retried_forever(client):
    """Otherwise one bad posting blocks every posting behind it."""
    db = get_db()
    broken = make_job(db, created=NOW + 100)
    db.execute("UPDATE jobs SET triage_error='boom' WHERE id=?", (broken,))
    db.commit()
    fine = make_job(db, created=NOW)

    assert triager.next_pending(db)['id'] == fine


def test_newest_is_judged_first(client):
    """A backlog, not a queue: the value of a verdict decays with the posting."""
    db = get_db()
    make_job(db, created=NOW - 5000)
    newest = make_job(db, created=NOW)
    assert triager.next_pending(db)['id'] == newest


def test_missing_job_is_not_found(client):
    assert triager.process_one('nope')['error'] == 'Not found'


# --------------------------------------------------------------------------
# Restore and reset — the audit trail is only useful if it can be acted on
# --------------------------------------------------------------------------

def test_restore_puts_a_rejected_posting_back(client):
    db = get_db()
    job_id = make_job(db, state='rejected')

    assert triager.restore(db, job_id) is True
    # 'kept', not 'pending' — pending would hand it straight back to the layer
    # that just rejected it.
    assert state_of(db, job_id)['triage_state'] == 'kept'


def test_restore_refuses_a_posting_that_was_not_rejected(client):
    db = get_db()
    job_id = make_job(db, state='kept')
    assert triager.restore(db, job_id) is False


def test_reset_clears_an_error_and_requeues(client):
    db = get_db()
    job_id = make_job(db, state='error')
    db.execute("UPDATE jobs SET triage_error='boom' WHERE id=?", (job_id,))
    db.commit()

    assert triager.reset_pending(db, job_id) is True
    row = state_of(db, job_id)
    assert row['triage_state'] == 'pending'
    assert row['triage_error'] is None


# --------------------------------------------------------------------------
# Re-sync interaction — the expensive rule to get wrong
# --------------------------------------------------------------------------

def _resync(db, description):
    sync.upsert_job(
        db, 'greenhouse',
        {'sourceId': 'gh-1', 'title': 'Backend Engineer', 'description': description},
        {'roles': [], 'skills': [], 'profile': {}},
    )
    db.commit()


def test_a_resync_with_identical_text_does_not_re_triage(client):
    """Boards re-list the same posting nightly, byte for byte.

    Without this the model would spend ~1,300 verdicts every night reproducing
    yesterday's answers.
    """
    db = get_db()
    _resync(db, 'We use Python.')
    job_id = db.execute("SELECT id FROM jobs WHERE source_id='gh-1'").fetchone()['id']
    db.execute(
        "UPDATE jobs SET triage_state='kept', triage_summary='A Python job.'"
        ' WHERE id=?', (job_id,)
    )
    db.commit()

    _resync(db, 'We use Python.')

    row = state_of(db, job_id)
    assert row['triage_state'] == 'kept'
    assert row['triage_summary'] == 'A Python job.'


def test_a_rewritten_posting_is_re_triaged(client):
    """The other half: a stale summary describes a job the posting no longer is."""
    db = get_db()
    _resync(db, 'We use Python.')
    job_id = db.execute("SELECT id FROM jobs WHERE source_id='gh-1'").fetchone()['id']
    db.execute(
        "UPDATE jobs SET triage_state='kept', triage_summary='A Python job.'"
        ' WHERE id=?', (job_id,)
    )
    db.commit()

    _resync(db, 'Actually this is now a Rust role.')

    row = state_of(db, job_id)
    assert row['triage_state'] == 'pending'
    assert row['triage_summary'] == ''


# --------------------------------------------------------------------------
# The feed and the filtered list
# --------------------------------------------------------------------------

def test_rejected_postings_are_absent_from_the_feed(client):
    db = get_db()
    make_job(db, title='Line Cook', state='rejected')
    kept = make_job(db, title='Backend Engineer', state='kept')

    feed = client.get('/api/jobs/feed').get_json()

    assert [j['id'] for j in feed] == [kept]


def test_pending_postings_stay_in_the_feed(client):
    """With the model off, the feed must behave as it did before triage existed
    rather than silently emptying."""
    db = get_db()
    job_id = make_job(db, state='pending')
    feed = client.get('/api/jobs/feed').get_json()
    assert [j['id'] for j in feed] == [job_id]


def test_the_filtered_list_shows_what_was_thrown_out(client):
    db = get_db()
    cook = make_job(db, title='Line Cook', state='rejected')
    make_job(db, title='Backend Engineer', state='kept')

    filtered = client.get('/api/jobs/filtered').get_json()

    assert [j['id'] for j in filtered] == [cook]


def test_strong_fit_sorts_above_stretch(client):
    db = get_db()
    stretch = make_job(db, state='kept')
    strong = make_job(db, state='kept')
    db.execute("UPDATE jobs SET triage_fit='stretch' WHERE id=?", (stretch,))
    db.execute("UPDATE jobs SET triage_fit='strong' WHERE id=?", (strong,))
    db.commit()

    feed = client.get('/api/jobs/feed').get_json()

    assert [j['id'] for j in feed] == [strong, stretch]


def test_restore_route_returns_the_job_to_the_feed(client):
    db = get_db()
    job_id = make_job(db, title='Line Cook', state='rejected')

    assert client.post(f'/api/jobs/{job_id}/triage/restore').status_code == 200

    feed = client.get('/api/jobs/feed').get_json()
    assert [j['id'] for j in feed] == [job_id]


def test_triage_status_counts_the_backlog(client):
    db = get_db()
    make_job(db, state='pending')
    make_job(db, title='Line Cook', state='rejected')

    status = client.get('/api/jobs/triage/status').get_json()

    assert status['pending'] == 1
    assert status['rejected'] == 1
    assert status['enabled'] is True


# --------------------------------------------------------------------------
# The worker gate
# --------------------------------------------------------------------------

def test_drain_defers_while_the_user_is_waiting(client):
    db = get_db()
    make_job(db)
    token = priority.begin('chat')
    try:
        assert triager.drain_once() is None
    finally:
        priority.end(token)


def test_drain_does_nothing_when_disabled(client):
    db = get_db()
    make_job(db)
    db.execute('UPDATE settings SET job_triage_enabled=0')
    db.commit()
    assert triager.drain_once() is None


def test_drain_submits_one_pending_posting(client, monkeypatch):
    db = get_db()
    job_id = make_job(db)
    monkeypatch.setattr(
        'backend.ai.job_triage.triage_posting',
        lambda *a, **k: {'relevant': True, 'reason': '', 'fit': 'possible',
                         'summary': 'x', 'flags': [], 'missingMustHaves': []},
    )

    assert triager.drain_once() == job_id
    triager.wait_idle(timeout=10)
    assert state_of(db, job_id)['triage_state'] == 'kept'


# --------------------------------------------------------------------------
# What is not worth a model call — both learned from the live database
# --------------------------------------------------------------------------

def test_a_posting_with_no_body_is_never_judged(client):
    """The backfilled rows were rebuilt from confirmation emails, which never
    carried the posting. Judging one means judging its title alone, which is
    exactly what the cascade exists to avoid."""
    db = get_db()
    make_job(db, description='')
    assert triager.next_pending(db) is None
    assert triager.pending_count(db) == 0


def test_a_posting_already_applied_to_is_never_judged(client):
    """It has left triage. The feed excludes it for the same reason, so a
    verdict on it could never be seen — on the live database this was 1,296 of
    1,370 pending rows, and roughly two hours of GPU."""
    db = get_db()
    job_id = make_job(db)
    db.execute(
        'INSERT INTO applications (id, job_id, status, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), job_id, 'submitted', NOW, NOW),
    )
    db.commit()

    assert triager.next_pending(db) is None
    assert triager.pending_count(db) == 0


def test_the_backlog_count_matches_what_the_worker_will_do(client):
    """One condition, shared — a status panel that disagrees with the worker is
    worse than no status panel."""
    db = get_db()
    make_job(db, description='')
    make_job(db, description='Real posting body.')
    assert triager.pending_count(db) == 1
    assert triager.next_pending(db) is not None


def test_the_gate_skips_postings_already_applied_to(client):
    """The filtered list is for what you are missing, and you are not missing a
    job you applied to."""
    db = get_db()
    job_id = make_job(db, title='Line Cook')
    db.execute(
        'INSERT INTO applications (id, job_id, status, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), job_id, 'submitted', NOW, NOW),
    )
    db.commit()

    assert triager.run_gate_sweep(db)['scanned'] == 0
    assert state_of(db, job_id)['triage_state'] == 'pending'
