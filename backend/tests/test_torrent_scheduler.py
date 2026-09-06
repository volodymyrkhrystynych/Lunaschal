"""The reconcile/purge tick. Pure decision, mocked client, real database."""
import datetime
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.torrent import client as torrent_client
from backend.torrent import scheduler

HEX = 'c9e15763f722f23e98a29decdfae341b98d53056'
DAY = 86400


def make_row(info_hash=HEX, *, retention_days=None, completed_at=None, added_at=1000):
    now = int(time.time())
    get_db().execute(
        'INSERT INTO torrents (id, info_hash, name, source, retention_days,'
        ' added_at, completed_at, created_at, updated_at)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (str(ULID()), info_hash, 'Debian ISO', 'magnet', retention_days,
         added_at, completed_at, now, now),
    )
    get_db().commit()


@pytest.fixture(autouse=True)
def db(isolated_db):
    # The orphan-confirmation memo is module-level, so it has to be reset
    # between tests or one test's missing hashes confirm another's.
    scheduler._previously_missing = set()
    return isolated_db


def test_completion_is_copied_onto_our_row_once(monkeypatch):
    """Read live it would vanish whenever the container's config is rebuilt,
    and retention is measured from it."""
    make_row()
    monkeypatch.setattr(torrent_client, 'torrents_info',
                        lambda hashes=None: [{'hash': HEX, 'completion_on': 1700}])
    assert scheduler.reconcile_once() == {'completed': 1, 'forgotten': 0}
    assert get_db().execute('SELECT completed_at FROM torrents').fetchone()[0] == 1700
    # Idempotent — a second pass must not rewrite it.
    assert scheduler.reconcile_once()['completed'] == 0


def test_an_unfinished_torrent_is_left_alone(monkeypatch):
    make_row()
    monkeypatch.setattr(torrent_client, 'torrents_info',
                        lambda hashes=None: [{'hash': HEX, 'completion_on': 0}])
    scheduler.reconcile_once()
    assert get_db().execute('SELECT completed_at FROM torrents').fetchone()[0] is None


def test_a_row_whose_torrent_left_the_client_is_forgotten(monkeypatch):
    """Otherwise it shows as a phantom entry that no button can affect — but
    only after a second confirming pass; see the next test."""
    make_row()
    monkeypatch.setattr(torrent_client, 'torrents_info', lambda hashes=None: [])
    assert scheduler.reconcile_once()['forgotten'] == 0
    assert scheduler.reconcile_once()['forgotten'] == 1
    assert get_db().execute('SELECT COUNT(*) FROM torrents').fetchone()[0] == 0


def test_a_momentarily_empty_client_does_not_wipe_every_row(monkeypatch):
    """qBittorrent answers torrents/info with [] for the first moment after it
    starts, before its resume data is loaded — and its container restarts
    whenever the VPN one does. Acting on that single observation would delete
    every note the user has written, on a routine restart."""
    make_row()
    state = {'live': []}
    monkeypatch.setattr(torrent_client, 'torrents_info', lambda hashes=None: state['live'])

    assert scheduler.reconcile_once()['forgotten'] == 0
    # ...and now it finishes starting up.
    state['live'] = [{'hash': HEX, 'completion_on': 0}]
    assert scheduler.reconcile_once()['forgotten'] == 0
    assert get_db().execute('SELECT COUNT(*) FROM torrents').fetchone()[0] == 1


def test_purge_deletes_the_files_too(monkeypatch):
    """A retention policy that keeps the bytes has not freed anything."""
    completed = int(time.time()) - 10 * DAY
    make_row(retention_days=7, completed_at=completed)
    deleted = []
    monkeypatch.setattr(torrent_client, 'delete',
                        lambda h, delete_files=False: deleted.append((h, delete_files)))
    assert scheduler.run_purge_sweep() == {'purged': 1}
    assert deleted == [(HEX, True)]
    assert get_db().execute('SELECT COUNT(*) FROM torrents').fetchone()[0] == 0


def test_a_failed_delete_keeps_the_row_so_the_next_sweep_retries(monkeypatch):
    """Dropping the row would strand the files with nothing tracking them."""
    make_row(retention_days=7, completed_at=int(time.time()) - 10 * DAY)

    def boom(h, delete_files=False):
        raise torrent_client.TorrentClientError('busy')

    monkeypatch.setattr(torrent_client, 'delete', boom)
    assert scheduler.run_purge_sweep() == {'purged': 0}
    assert get_db().execute('SELECT COUNT(*) FROM torrents').fetchone()[0] == 1


def test_a_torrent_with_no_retention_is_never_purged(monkeypatch):
    make_row(retention_days=None, completed_at=1)
    monkeypatch.setattr(torrent_client, 'delete',
                        lambda *a, **kw: pytest.fail('must not delete'))
    assert scheduler.run_purge_sweep() == {'purged': 0}


def test_a_stopped_stack_skips_the_whole_tick_quietly(monkeypatch):
    """Not running is a normal state — the user starts the stack when they
    want it — so it must not log or fail every minute."""
    def boom(hashes=None):
        raise torrent_client.TorrentClientUnavailable('refused')

    monkeypatch.setattr(torrent_client, 'torrents_info', boom)
    results, last = scheduler.tick(last_purge_date=None)
    assert results == {}
    assert last is None


def test_purge_runs_once_a_day_inside_its_window(monkeypatch):
    monkeypatch.setattr(torrent_client, 'torrents_info', lambda hashes=None: [])
    in_window = datetime.datetime(2026, 9, 5, 8, 30)

    results, last = scheduler.tick(now=in_window, last_purge_date=None)
    assert 'purge' in results and last == in_window.date()

    # Same day, still in the window: not again.
    results, last = scheduler.tick(now=in_window, last_purge_date=last)
    assert 'purge' not in results


def test_purge_does_not_run_outside_its_window(monkeypatch):
    """08:00–09:00, after the jobs file purge, so a large delete never overlaps
    a backup or a model-using pass."""
    monkeypatch.setattr(torrent_client, 'torrents_info', lambda hashes=None: [])
    results, last = scheduler.tick(now=datetime.datetime(2026, 9, 5, 14, 0),
                                   last_purge_date=None)
    assert 'purge' not in results
    assert last is None


def test_reconcile_still_runs_outside_the_purge_window(monkeypatch):
    monkeypatch.setattr(torrent_client, 'torrents_info', lambda hashes=None: [])
    results, _ = scheduler.tick(now=datetime.datetime(2026, 9, 5, 14, 0),
                                last_purge_date=None)
    assert results['reconcile'] == {'completed': 0, 'forgotten': 0}
