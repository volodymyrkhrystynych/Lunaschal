"""The nightly pass: pull, graph, scan, write — in that order, per repository.

The order is the whole test. A snapshot of a tree that was not pulled describes
yesterday, and a code note written against that snapshot cites lines that have
moved.
"""
import pytest

from backend.db.connection import get_db
from backend.research import repo_scheduler

pytestmark = pytest.mark.usefixtures('isolated_db')


def _repo(repo_id, slug, state='ready'):
    get_db().execute(
        'INSERT INTO repos(id, slug, name, remote_url, branch, clone_state,'
        ' is_default, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (repo_id, slug, slug.title(), f'https://h/o/{slug}.git', '', state, 0, 1, 1),
    )
    get_db().commit()


@pytest.fixture
def trace(monkeypatch):
    """Record the order of the four steps without running any of them."""
    seen = []

    class FakeJob:
        @staticmethod
        def run_pull(repo_id, cancel=None):
            seen.append(('pull', repo_id))
            return {'ok': True}

        @staticmethod
        def run_import(repo_id, cancel=None):
            seen.append(('import', repo_id))
            return {'ok': True}

    class FakeCodeWiki:
        @staticmethod
        def run_code_wiki(repo_id, cancel=None):
            seen.append(('articles', repo_id))
            return {'written': ['a'], 'skipped': 0}

    def fake_snapshot(*args, repo_id=None, **kwargs):
        seen.append(('snapshot', repo_id))
        return {'id': 's1'}

    monkeypatch.setattr(repo_scheduler, 'run_repo_snapshot', fake_snapshot)
    monkeypatch.setattr('backend.repos.job.run_pull', FakeJob.run_pull)
    monkeypatch.setattr('backend.repos.job.run_import', FakeJob.run_import)
    monkeypatch.setattr('backend.research.code_wiki.run_code_wiki',
                        FakeCodeWiki.run_code_wiki)
    return seen


def test_the_four_steps_run_in_order(client, trace):
    _repo('r1', 'alpha')
    repo_scheduler.run_nightly()
    # The graph refresh lives inside run_pull (backend/repos/job.py), which is
    # what keeps import and pull from drifting apart.
    assert trace[:3] == [('pull', 'r1'), ('snapshot', 'r1'), ('articles', 'r1')]


def test_this_apps_own_checkout_is_still_scanned_last(client, trace):
    """It is not a registered repo and has no clone to pull, but it still gets
    the snapshot it always got."""
    _repo('r1', 'alpha')
    repo_scheduler.run_nightly()
    assert trace[-1] == ('snapshot', None)


def test_with_no_repos_registered_only_the_self_scan_runs(client, trace):
    repo_scheduler.run_nightly()
    assert trace == [('snapshot', None)]


def test_a_repo_that_never_cloned_gets_its_clone_rather_than_being_skipped(client, trace):
    _repo('r1', 'alpha', state='error')
    repo_scheduler.run_nightly()
    assert ('import', 'r1') in trace
    assert ('articles', 'r1') in trace


def test_a_failed_pull_stops_that_repo_before_it_scans_a_stale_tree(client, monkeypatch):
    """Scanning after a failed pull would produce a snapshot describing
    yesterday and articles citing lines that have moved."""
    _repo('r1', 'alpha')
    seen = []
    monkeypatch.setattr('backend.repos.job.run_pull',
                        lambda rid, cancel=None: {'ok': False, 'error': 'no network'})
    monkeypatch.setattr(repo_scheduler, 'run_repo_snapshot',
                        lambda *a, repo_id=None, **k: seen.append(repo_id))
    monkeypatch.setattr('backend.research.code_wiki.run_code_wiki',
                        lambda rid, cancel=None: seen.append('articles'))

    result = repo_scheduler.run_nightly()
    assert result['alpha'] == {'pulled': False, 'error': 'no network'}
    assert 'articles' not in seen
    assert seen == [None]  # only the self scan


def test_one_repos_failure_does_not_cost_the_others_their_night(client, monkeypatch):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    done = []

    def flaky(repo_id, cancel=None):
        if repo_id == 'r1':
            raise RuntimeError('disk full')
        done.append(repo_id)
        return {'ok': True}

    monkeypatch.setattr('backend.repos.job.run_pull', flaky)
    monkeypatch.setattr(repo_scheduler, 'run_repo_snapshot', lambda *a, **k: None)
    monkeypatch.setattr('backend.research.code_wiki.run_code_wiki',
                        lambda rid, cancel=None: {'written': [], 'skipped': 0})

    result = repo_scheduler.run_nightly()
    assert 'disk full' in result['alpha']['error']
    assert done == ['r2']


def test_cancelling_stops_before_the_next_repo(client, trace):
    import threading
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    cancel = threading.Event()
    cancel.set()

    repo_scheduler.run_nightly(cancel=cancel)
    # No repo work at all, but the self scan still happens — it is cheap and
    # touches nothing the cancel was about.
    assert trace == [('snapshot', None)]


# --- The window is unchanged ---

def test_the_window_is_still_03_to_05(client):
    from datetime import datetime
    assert repo_scheduler.in_window(3, datetime(2026, 8, 27, 3, 30))
    assert repo_scheduler.in_window(3, datetime(2026, 8, 27, 4, 59))
    # 02:00-03:00 is the chat-title sweep, 05:00-07:00 the briefing.
    assert not repo_scheduler.in_window(3, datetime(2026, 8, 27, 2, 59))
    assert not repo_scheduler.in_window(3, datetime(2026, 8, 27, 5, 0))


def test_it_runs_once_a_day(client):
    from datetime import datetime
    now = datetime(2026, 8, 27, 3, 30)
    assert repo_scheduler.should_run(True, 3, now, None)
    assert not repo_scheduler.should_run(True, 3, now, now.date())
    assert not repo_scheduler.should_run(False, 3, now, None)
