"""Tests for `backend.git_sync` — the git CLI wrapper behind the Files-tab Sync.

These drive the *real* git CLI against a bare repo + work trees created in
`tmp_path`. No network: the "remote" is a local bare repo on disk, which stands
in for the user's self-hosted SSH remote. The whole module is skipped if git is
not installed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import git_sync

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')


def _init_worktree(path: Path, remote_url: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git_sync.init(path, 'main')
    git_sync.set_remote(path, remote_url)
    return path


@pytest.fixture
def remote(tmp_path: Path) -> str:
    """A bare repo standing in for the self-hosted remote."""
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(bare)], check=True, capture_output=True)
    return str(bare)


@pytest.fixture
def repo_a(tmp_path: Path, remote: str) -> Path:
    return _init_worktree(tmp_path / 'a', remote)


def test_init_creates_repo_and_gitignore(repo_a: Path):
    assert git_sync.is_repo(repo_a)
    gitignore = (repo_a / '.gitignore').read_text()
    assert '.trash/' in gitignore


def test_commit_all_then_noop(repo_a: Path):
    (repo_a / 'note.md').write_text('hello')
    assert git_sync.commit_all(repo_a) is True
    # Nothing changed since last commit -> no-op success.
    assert git_sync.commit_all(repo_a) is False


def test_remote_roundtrip(repo_a: Path, remote: str):
    assert git_sync.get_remote(repo_a) == remote


def test_first_push_sets_upstream(repo_a: Path):
    (repo_a / 'note.md').write_text('hello')
    git_sync.commit_all(repo_a)
    st = git_sync.status(repo_a)
    assert st['has_upstream'] is False
    res = git_sync.push(repo_a, 'main', set_upstream=True)
    assert res['ok'], res
    assert git_sync.status(repo_a)['has_upstream'] is True


def test_dirty_detection(repo_a: Path):
    (repo_a / 'note.md').write_text('hello')
    git_sync.commit_all(repo_a)
    assert git_sync.status(repo_a)['dirty'] is False
    (repo_a / 'note.md').write_text('changed')
    assert git_sync.status(repo_a)['dirty'] is True


def test_fresh_repo_status_has_no_upstream(repo_a: Path):
    st = git_sync.status(repo_a)
    assert st['ahead'] is None
    assert st['behind'] is None
    assert st['has_upstream'] is False


def test_detached_head_reported(repo_a: Path):
    (repo_a / 'note.md').write_text('hello')
    git_sync.commit_all(repo_a)
    sha = git_sync._run(['rev-parse', 'HEAD'], repo_a).stdout.strip()
    git_sync._run(['checkout', sha], repo_a)
    st = git_sync.status(repo_a)
    assert st['detached'] is True
    assert git_sync.current_branch(repo_a) == 'HEAD'


def test_second_clone_pull_and_ahead_behind(tmp_path: Path, remote: str, repo_a: Path):
    # A publishes an initial commit.
    (repo_a / 'note.md').write_text('v1')
    git_sync.commit_all(repo_a)
    assert git_sync.push(repo_a, 'main', set_upstream=True)['ok']

    # B onboards by cloning (independent init would create unrelated histories).
    repo_b = tmp_path / 'b'
    assert git_sync.clone(remote, repo_b, 'main')['ok']
    (repo_b / 'note.md').write_text('v2')
    git_sync.commit_all(repo_b)
    assert git_sync.push(repo_b, 'main')['ok']

    # A now sees itself behind by one, then pulls the change.
    git_sync._run(['fetch', 'origin'], repo_a)
    assert git_sync.status(repo_a)['behind'] == 1
    assert git_sync.pull(repo_a, 'main')['ok']
    assert (repo_a / 'note.md').read_text() == 'v2'


def test_conflict_detection_and_resolution(tmp_path: Path, remote: str, repo_a: Path):
    (repo_a / 'note.md').write_text('base line\n')
    git_sync.commit_all(repo_a)
    assert git_sync.push(repo_a, 'main', set_upstream=True)['ok']

    repo_b = tmp_path / 'b'
    assert git_sync.clone(remote, repo_b, 'main')['ok']

    # Both edit the same line; B pushes first.
    (repo_b / 'note.md').write_text('B edit\n')
    git_sync.commit_all(repo_b)
    assert git_sync.push(repo_b, 'main')['ok']

    (repo_a / 'note.md').write_text('A edit\n')
    git_sync.commit_all(repo_a)
    pull_res = git_sync.pull(repo_a, 'main')
    assert pull_res['ok'] is False
    assert pull_res['conflicted'] is True
    assert git_sync.status(repo_a)['conflicted'] is True

    # User resolves the markers by hand, commits, and pushes.
    (repo_a / 'note.md').write_text('merged\n')
    git_sync.commit_all(repo_a)
    assert git_sync.status(repo_a)['conflicted'] is False
    assert git_sync.push(repo_a, 'main')['ok']


def test_sync_first_push_to_empty_remote(tmp_path: Path, remote: str, repo_a: Path):
    # Remote is empty: sync() must skip the pull and push -u without error.
    (repo_a / 'note.md').write_text('hello')
    result = git_sync.sync(repo_a, 'main')
    assert result == {'ok': True}
    # A second machine clones and receives the file.
    repo_b = tmp_path / 'b'
    assert git_sync.clone(remote, repo_b, 'main')['ok']
    assert (repo_b / 'note.md').read_text() == 'hello'


def test_sync_roundtrip_between_two_machines(tmp_path: Path, remote: str, repo_a: Path):
    (repo_a / 'note.md').write_text('from A')
    assert git_sync.sync(repo_a, 'main') == {'ok': True}

    repo_b = tmp_path / 'b'
    assert git_sync.clone(remote, repo_b, 'main')['ok']
    (repo_b / 'note2.md').write_text('from B')
    assert git_sync.sync(repo_b, 'main') == {'ok': True}

    # A syncs and should now have B's file.
    assert git_sync.sync(repo_a, 'main') == {'ok': True}
    assert (repo_a / 'note2.md').read_text() == 'from B'


def test_remote_has_content(tmp_path: Path, remote: str, repo_a: Path):
    assert git_sync.remote_has_content(remote) is False
    (repo_a / 'note.md').write_text('hello')
    git_sync.sync(repo_a, 'main')
    assert git_sync.remote_has_content(remote) is True
