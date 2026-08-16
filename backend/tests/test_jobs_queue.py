"""The resume backburner: selection order, the idle gate, and failure recording.

Tailoring is stubbed throughout — what is under test is the queue discipline,
not the model. The failure tests matter most: a queued resume that never
generated has to be *visible*, or an application sits in 'draft' looking like
the user simply forgot about it.
"""
import threading
import time

import pytest

from backend.ai import priority
from backend.db.connection import get_db
from backend.jobs import build, queue

NOW = int(time.time())


@pytest.fixture(autouse=True)
def clean_worker(client):
    """Drain the worker before the DB goes away.

    Depending on `client` is load-bearing, not incidental: finalizers run in
    reverse setup order, so without it this fixture can tear down *after*
    conftest closes the connection — and a worker thread mid-query against a
    closed sqlite handle segfaults the interpreter rather than raising.
    """
    queue.reset()
    yield
    queue.wait_idle(timeout=5)
    queue.reset()


@pytest.fixture(autouse=True)
def quiet_priority():
    """No interactive marks in flight, and long past any grace period."""
    priority._marks.clear()
    priority._released_at = time.monotonic() - 3600
    yield
    priority._marks.clear()


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBS_ROOT', str(tmp_path / 'jobs'))
    return tmp_path / 'jobs'


@pytest.fixture
def profile(client):
    role_id = client.post('/api/jobs/profile/roles', json={
        'company': 'Acme', 'title': 'Engineer', 'ord': 0,
    }).get_json()['id']
    client.post('/api/jobs/profile/bullets', json={
        'roleId': role_id, 'text': 'Built billing in Python.', 'ord': 0,
    })
    client.post('/api/jobs/profile/skills', json={'name': 'Python'})
    return role_id


def make_job(client, title='Python Engineer'):
    return client.post('/api/jobs', json={
        'title': title, 'company': 'Acme', 'description': 'We need Python.',
    }).get_json()['id']


def queue_job(client, job_id, steer=''):
    return client.post(f'/api/jobs/{job_id}/queue', json={'steer': steer}).get_json()


def stub_tailor(monkeypatch):
    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: {
        'summary': 'Backend engineer.',
        'bullets': [{'index': 0, 'text': 'Built billing in Python.',
                     'original': 'Built billing in Python.', 'rewritten': False}],
        'keywords': {'matched': ['python'], 'missing': []},
    })


# --------------------------------------------------------------------------
# Queueing is instant
# --------------------------------------------------------------------------

def test_queueing_creates_a_draft_application_and_returns(client, profile, monkeypatch):
    """The phone half of the feature: tapping Queue must not wait on a model."""
    def explode(*a, **k):
        raise AssertionError('queueing must not call the model')

    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', explode)
    job_id = make_job(client)
    application = queue_job(client, job_id)

    assert application['status'] == 'draft'
    assert application['queuedAt'] is not None


def test_queueing_the_same_job_twice_reuses_the_application(client, profile):
    job_id = make_job(client)
    first = queue_job(client, job_id)
    second = queue_job(client, job_id)
    assert first['id'] == second['id']


def test_queueing_stores_the_steer(client, profile):
    job_id = make_job(client)
    application = queue_job(client, job_id, steer='Lead with the payments work.')
    assert application['steer'] == 'Lead with the payments work.'


def test_requeueing_without_a_steer_keeps_the_old_one(client, profile):
    """The second tap is 'just autofill' — it must not wipe what was dictated."""
    job_id = make_job(client)
    queue_job(client, job_id, steer='Emphasise Python.')
    again = queue_job(client, job_id, steer='')
    assert again['steer'] == 'Emphasise Python.'


def test_queueing_an_unknown_job_is_a_404(client):
    assert client.post('/api/jobs/nope/queue', json={}).status_code == 404


# --------------------------------------------------------------------------
# Selection order
# --------------------------------------------------------------------------

def test_the_oldest_queued_application_goes_first(client, profile):
    db = get_db()
    first = queue_job(client, make_job(client, 'First'))['id']
    second = queue_job(client, make_job(client, 'Second'))['id']
    # Same-second queueing is realistic; make the order unambiguous.
    db.execute('UPDATE applications SET queued_at=? WHERE id=?', (NOW - 100, first))
    db.execute('UPDATE applications SET queued_at=? WHERE id=?', (NOW, second))
    db.commit()

    assert queue.next_queued(db)['id'] == first


def test_an_application_with_a_resume_is_no_longer_queued(client, profile, jobs_root,
                                                          monkeypatch):
    stub_tailor(monkeypatch)
    db = get_db()
    application_id = queue_job(client, make_job(client))['id']

    queue.process_one(application_id)

    assert queue.next_queued(db) is None


def test_an_unqueued_draft_is_not_picked_up(client, profile):
    """Starting an application by hand is not the same as queueing it."""
    db = get_db()
    job_id = make_job(client)
    client.post('/api/jobs/applications', json={'jobId': job_id})
    assert queue.next_queued(db) is None


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------

def test_a_processed_application_becomes_ready(client, profile, jobs_root, monkeypatch):
    stub_tailor(monkeypatch)
    application_id = queue_job(client, make_job(client))['id']

    result = queue.process_one(application_id)

    assert result['ok'] is True
    row = get_db().execute('SELECT status, queue_error FROM applications WHERE id=?',
                           (application_id,)).fetchone()
    assert row['status'] == 'ready'
    assert row['queue_error'] is None


def test_a_failure_is_recorded_on_the_application(client, profile, jobs_root, monkeypatch):
    """Otherwise the application sits in 'draft' with no explanation."""
    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    application_id = queue_job(client, make_job(client))['id']

    result = queue.process_one(application_id)

    row = get_db().execute('SELECT status, queue_error FROM applications WHERE id=?',
                           (application_id,)).fetchone()
    assert result['ok'] is False
    assert row['status'] == 'draft'
    assert 'unavailable' in row['queue_error']


def test_the_worker_survives_a_failure_and_processes_the_next_item(client, profile,
                                                                   jobs_root, monkeypatch):
    db = get_db()
    bad = queue_job(client, make_job(client, 'Bad'))['id']
    good = queue_job(client, make_job(client, 'Good'))['id']
    db.execute('UPDATE applications SET queued_at=? WHERE id=?', (NOW - 100, bad))
    db.execute('UPDATE applications SET queued_at=? WHERE id=?', (NOW, good))
    db.commit()

    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    queue.process_one(bad)

    stub_tailor(monkeypatch)
    assert queue.process_one(good)['ok'] is True


def test_requeueing_clears_a_previous_error(client, profile, jobs_root, monkeypatch):
    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    job_id = make_job(client)
    application_id = queue_job(client, job_id)['id']
    queue.process_one(application_id)

    again = queue_job(client, job_id)

    assert again['queueError'] is None


def test_a_processed_application_records_no_open_transaction(client, profile,
                                                             jobs_root, monkeypatch):
    """The standing rule: never hold a transaction across the model call.

    `tailor_resume` asserts the connection is not mid-transaction at the moment
    it is invoked, which is exactly when a stray uncommitted write would show.
    """
    db = get_db()

    def checking_tailor(*a, **k):
        assert not db.in_transaction, 'a transaction was open across the model call'
        return {'summary': 'S.', 'bullets': [], 'keywords': {}}

    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', checking_tailor)
    application_id = queue_job(client, make_job(client), steer='Some steer.')['id']

    assert queue.process_one(application_id)['ok'] is True


# --------------------------------------------------------------------------
# The single slot and the idle gate
# --------------------------------------------------------------------------

def test_only_one_application_runs_at_a_time(client, profile, jobs_root, monkeypatch):
    """The block is placed at `build_resume_version` rather than inside
    tailoring so the held worker thread never touches the shared sqlite
    connection while the main thread is still using it."""
    release = threading.Event()

    def blocking_build(*a, **k):
        release.wait(5)
        return {'id': 'v1'}

    monkeypatch.setattr('backend.jobs.build.build_resume_version', blocking_build)
    first = queue_job(client, make_job(client, 'One'))['id']
    second = queue_job(client, make_job(client, 'Two'))['id']

    try:
        assert queue.submit(first) is True
        assert queue.submit(second) is False
    finally:
        release.set()
    queue.wait_idle(timeout=5)


def test_the_drain_defers_while_the_user_is_busy(client, profile):
    """A tailoring pass is minutes of the model; it waits for a quiet machine."""
    application_id = queue_job(client, make_job(client))['id']
    token = priority.begin('chat')
    try:
        assert queue.drain_once() is None
    finally:
        priority.end(token)


def test_the_drain_defers_during_the_grace_period(client, profile):
    queue_job(client, make_job(client))
    priority._released_at = time.monotonic()   # a chat just finished
    assert queue.drain_once() is None


def test_the_drain_submits_when_quiet(client, profile, jobs_root, monkeypatch):
    stub_tailor(monkeypatch)
    application_id = queue_job(client, make_job(client))['id']

    assert queue.drain_once() == application_id
    queue.wait_idle(timeout=5)


def test_the_drain_is_a_noop_with_nothing_queued(client, profile):
    assert queue.drain_once() is None


def test_the_manual_drain_ignores_the_idle_gate(client, profile, jobs_root, monkeypatch):
    """Asking for it by hand *is* the signal that now is a good time."""
    stub_tailor(monkeypatch)
    application_id = queue_job(client, make_job(client))['id']

    token = priority.begin('chat')
    try:
        response = client.post('/api/jobs/queue/drain').get_json()
    finally:
        priority.end(token)

    assert response['submitted'] == application_id
    queue.wait_idle(timeout=5)


def test_queue_status_counts_pending_and_failed(client, profile, jobs_root, monkeypatch):
    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    failed = queue_job(client, make_job(client, 'Fails'))['id']
    queue.process_one(failed)
    queue_job(client, make_job(client, 'Waiting'))

    status = client.get('/api/jobs/queue/status').get_json()

    # The failed one is not "waiting" — nothing will pick it up again without
    # an explicit re-queue, so counting it as pending would be a false promise.
    assert status['pending'] == 1
    assert status['failed'] == 1


def test_a_failure_does_not_block_the_queue_behind_it(client, profile, jobs_root,
                                                      monkeypatch):
    """One posting the model chokes on must not be retried forever while
    everything queued behind it is never built."""
    db = get_db()
    bad = queue_job(client, make_job(client, 'Bad'))['id']
    good = queue_job(client, make_job(client, 'Good'))['id']
    db.execute('UPDATE applications SET queued_at=? WHERE id=?', (NOW - 100, bad))
    db.execute('UPDATE applications SET queued_at=? WHERE id=?', (NOW, good))
    db.commit()

    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    queue.process_one(bad)

    assert queue.next_queued(db)['id'] == good


def test_requeueing_a_failure_puts_it_back_in_line(client, profile, jobs_root,
                                                   monkeypatch):
    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    job_id = make_job(client)
    application_id = queue_job(client, job_id)['id']
    queue.process_one(application_id)
    assert queue.next_queued(get_db()) is None

    queue_job(client, job_id)

    assert queue.next_queued(get_db())['id'] == application_id


# --------------------------------------------------------------------------
# The shared build path
# --------------------------------------------------------------------------

def test_build_raises_rather_than_writing_a_fallback_resume(client, profile,
                                                            jobs_root, monkeypatch):
    """A resume built without the model must never be indistinguishable from
    one built with it."""
    monkeypatch.setattr('backend.jobs.tailor.tailor_resume', lambda *a, **k: None)
    application_id = queue_job(client, make_job(client))['id']

    with pytest.raises(build.TailoringUnavailable):
        build.build_resume_version(get_db(), application_id)

    assert get_db().execute(
        'SELECT COUNT(*) AS c FROM resume_versions WHERE application_id=?',
        (application_id,)).fetchone()['c'] == 0


def test_build_reports_an_empty_profile_separately(client, jobs_root):
    """The fix is the user's, not a retry — so it is a different exception."""
    job_id = make_job(client)
    application_id = queue_job(client, job_id)['id']

    with pytest.raises(build.ProfileEmpty):
        build.build_resume_version(get_db(), application_id)
