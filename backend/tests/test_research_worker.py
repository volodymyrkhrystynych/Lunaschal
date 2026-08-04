"""The research executor: one job at a time, cancellable, never fatal."""
import threading
import time

import pytest

from backend.db.connection import get_db
from backend.research import agent, worker


@pytest.fixture(autouse=True)
def clean_worker():
    worker.reset()
    yield
    worker.cancel()
    worker.wait_idle()
    worker.reset()


def test_idle_status():
    assert worker.status() == {'running': False, 'current': None, 'last': None}


def test_a_job_runs_and_is_recorded():
    ran = threading.Event()
    assert worker.submit('assess', lambda cancel: ran.set(), target='i1') is True
    assert ran.wait(5)
    assert worker.wait_idle()

    last = worker.status()['last']
    assert last['kind'] == 'assess'
    assert last['target'] == 'i1'
    assert last['error'] is None
    assert last['cancelled'] is False


def test_status_reports_the_running_job():
    release = threading.Event()
    worker.submit('research', lambda cancel: release.wait(5), target='i2')

    for _ in range(200):
        if worker.status()['running']:
            break
        time.sleep(0.01)
    current = worker.status()['current']
    assert current['kind'] == 'research'
    assert current['target'] == 'i2'

    release.set()
    assert worker.wait_idle()


def test_only_one_job_at_a_time():
    """A second research thread would double the slot contention the priority
    gate exists to avoid."""
    release = threading.Event()
    assert worker.submit('research', lambda cancel: release.wait(5)) is True

    for _ in range(200):
        if worker.status()['running']:
            break
        time.sleep(0.01)
    assert worker.submit('assess', lambda cancel: None) is False

    release.set()
    assert worker.wait_idle()
    # Once idle, the next job is accepted.
    assert worker.submit('assess', lambda cancel: None) is True
    assert worker.wait_idle()


def test_a_failing_job_does_not_kill_the_worker():
    def boom(cancel):
        raise RuntimeError('llama-server is down')

    worker.submit('research', boom)
    assert worker.wait_idle()
    assert 'llama-server is down' in worker.status()['last']['error']

    # The executor survives, so the next job still runs.
    ran = threading.Event()
    worker.submit('assess', lambda cancel: ran.set())
    assert ran.wait(5)


def test_cancel_is_false_when_idle():
    assert worker.cancel() is False


def test_cancelling_stops_the_job_at_its_next_checkpoint():
    started = threading.Event()
    steps = []

    def long_job(cancel):
        checkpoint = agent.make_checkpoint(cancel=cancel, gate=False)
        started.set()
        for i in range(100):
            checkpoint()          # raises Cancelled once the event is set
            steps.append(i)
            time.sleep(0.01)

    worker.submit('research', long_job)
    assert started.wait(5)
    time.sleep(0.05)
    assert worker.cancel() is True
    assert worker.wait_idle()

    last = worker.status()['last']
    assert last['cancelled'] is True
    assert last['error'] is None, 'cancellation is not a failure'
    assert 0 < len(steps) < 100, 'stopped partway, not at the end'


def test_cancel_flag_is_cleared_for_the_next_job():
    worker.submit('research', lambda cancel: None)
    assert worker.wait_idle()
    worker.cancel()  # idle, so a no-op, but the flag must not stick

    saw_cancelled = []
    worker.submit('assess', lambda cancel: saw_cancelled.append(cancel.is_set()))
    assert worker.wait_idle()
    assert saw_cancelled == [False]


def test_a_job_leaves_no_open_transaction(client):
    """get_db() is one process-global connection, so a transaction left open
    here would be committed by whatever request handler commits next."""
    in_transaction = []

    def job(cancel):
        db = get_db()
        db.execute(
            "INSERT INTO wiki_articles(id, slug, title, summary, content,"
            " revision, locked, created_at, updated_at)"
            " VALUES ('w1','s','T','','',1,0,1,1)"
        )
        db.commit()
        in_transaction.append(db.in_transaction)

    worker.submit('research', job)
    assert worker.wait_idle()
    assert in_transaction == [False]
    assert worker.status()['last']['error'] is None
