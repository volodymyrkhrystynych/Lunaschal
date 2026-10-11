"""Periodic discovery uses the same persistent queues as manual imports."""
from unittest.mock import Mock

import pytest

from backend.db.connection import get_db
from backend.fanfic import alert_scheduler as scheduler, collections, download, pacing


@pytest.fixture
def workers(monkeypatch):
    drain, scans = Mock(), Mock()
    monkeypatch.setattr(download, 'start_drain', drain)
    monkeypatch.setattr(collections, 'start_scans', scans)
    return drain, scans


def test_poll_without_sessions_makes_no_requests(workers, monkeypatch):
    fetch = Mock(side_effect=AssertionError('unexpected network'))
    monkeypatch.setattr(download, '_fetch', fetch)
    assert scheduler.poll()['alertsSeen'] == 0
    fetch.assert_not_called()
    workers[0].assert_called_once()
    workers[1].assert_not_called()


def test_poll_queues_ffn_and_preserves_active_checkpoint(workers):
    db = get_db()
    db.execute('INSERT INTO site_cookies(domain,cookie,updated_at) VALUES (?,?,0)', (pacing.DOMAIN, 'fake'))
    db.commit()
    scheduler.poll()
    row = db.execute('SELECT * FROM fanfic_collection_scans').fetchone()
    assert row['collection'] == 'all' and row['status'] == 'pending'
    db.execute('UPDATE fanfic_collection_scans SET pages=4,retry_after=9999999999 WHERE id=?', (row['id'],))
    db.commit()
    scheduler.poll()
    assert db.execute('SELECT pages,retry_after FROM fanfic_collection_scans').fetchone()[:] == (4, 9999999999)
    db.execute("UPDATE fanfic_collection_scans SET status='complete'")
    db.commit()
    scheduler.poll()
    row2 = db.execute('SELECT * FROM fanfic_collection_scans').fetchone()
    assert row2['id'] == row['id'] and row2['pages'] == 0 and row2['status'] == 'pending'


def test_browser_mode_needs_no_exported_cookie(workers, monkeypatch):
    monkeypatch.setattr(scheduler.browser, 'enabled', lambda: True)
    scheduler.poll()
    assert get_db().execute('SELECT site FROM fanfic_collection_scans').fetchone()[0] == pacing.DOMAIN


def test_ffn_pause_does_not_stop_forum_queue(workers):
    db = get_db()
    db.execute('INSERT INTO site_cookies(domain,cookie,updated_at) VALUES (?,?,0)', (pacing.DOMAIN, 'fake'))
    db.commit()
    pacing.pause()
    scheduler.poll()
    assert db.execute('SELECT COUNT(*) FROM fanfic_collection_scans').fetchone()[0] == 0
    workers[0].assert_called_once()


def test_scheduler_guard_and_interval(monkeypatch):
    thread = Mock()
    monkeypatch.setattr(scheduler.threading, 'Thread', thread)
    monkeypatch.setattr(scheduler, '_started', False)
    scheduler.start_scheduler()
    thread.assert_not_called()
    monkeypatch.delenv('LUNASCHAL_NO_SCHEDULERS')
    scheduler.start_scheduler()
    scheduler.start_scheduler()
    thread.assert_called_once()
    sleeps = []
    class StopLoop(BaseException):
        pass
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopLoop
    monkeypatch.setattr(scheduler.time, 'sleep', sleep)
    monkeypatch.setattr(scheduler, 'poll', Mock(side_effect=RuntimeError('one failed poll')))
    with pytest.raises(StopLoop):
        thread.call_args.kwargs['target']()
    assert sleeps[0] == 60
    assert 1800 <= sleeps[1] <= 5400
