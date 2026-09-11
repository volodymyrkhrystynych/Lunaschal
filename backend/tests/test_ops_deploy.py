"""backend/ops/deploy.py must never let auto-deploy touch a feature branch or a
dirty tree — this is what keeps the watcher from clobbering in-progress work on
the desktop machine that doubles as a dev box.
"""

import os
import subprocess

import pytest

from backend.ops.deploy import needs_deploy


def test_deploys_clean_main_behind_origin():
    assert needs_deploy('main', False, 'aaa', 'bbb') == 'deploy'


def test_skips_feature_branch_even_if_behind():
    assert needs_deploy('feat/whatever', False, 'aaa', 'bbb') == 'skip-branch'


def test_skips_dirty_main_even_if_behind():
    assert needs_deploy('main', True, 'aaa', 'bbb') == 'skip-dirty'


def test_dirty_feature_branch_reports_branch_reason_first():
    assert needs_deploy('feat/whatever', True, 'aaa', 'bbb') == 'skip-branch'


def test_up_to_date_clean_main():
    assert needs_deploy('main', False, 'aaa', 'aaa') == 'up-to-date'


def test_up_to_date_wins_over_dirty_check():
    # Nothing to pull, so dirty-tree state is irrelevant either way.
    assert needs_deploy('main', True, 'aaa', 'aaa') == 'up-to-date'


def test_local_commit_ahead_of_origin_is_not_a_deploy():
    # An unpushed merge on main: the shas differ, but origin has nothing new.
    # Reporting 'deploy' here makes deploy-check.sh pull nothing and then
    # rebuild + restart the production window on every 5-minute tick.
    assert needs_deploy('main', False, 'bbb', 'aaa', local_ahead=True) == 'ahead'


def test_ahead_reported_even_when_tree_is_dirty():
    assert needs_deploy('main', True, 'bbb', 'aaa', local_ahead=True) == 'ahead'


def test_behind_origin_still_deploys_when_not_ahead():
    assert needs_deploy('main', False, 'aaa', 'bbb', local_ahead=False) == 'deploy'


def test_ahead_on_a_feature_branch_still_reports_branch_first():
    assert needs_deploy('feat/whatever', False, 'bbb', 'aaa', local_ahead=True) == 'skip-branch'


# --- What ops/deploy-check.sh feeds into `dirty` -------------------------------
#
# The decision logic above is pure, but *whether* --dirty gets passed is one
# `git status` line in ops/deploy-check.sh, and getting it wrong is what froze
# production behind a few stray untracked files while every tick exited 0. These
# pin the porcelain contract that line depends on, against a throwaway repo.


def _git(repo, *args):
    return subprocess.run(
        ['git', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        # Keep the developer's own git config (hooks, gpg signing, templates)
        # out of a repo that only exists to answer one question.
        env={
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(repo),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'test',
            'GIT_AUTHOR_EMAIL': 'test@example.com',
            'GIT_COMMITTER_NAME': 'test',
            'GIT_COMMITTER_EMAIL': 'test@example.com',
        },
    ).stdout


def _porcelain(repo, *, untracked: bool) -> str:
    args = ['status', '--porcelain']
    if not untracked:
        args.append('--untracked-files=no')
    return _git(repo, *args).strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / 'repo'
    path.mkdir()
    _git(path, 'init', '-q', '-b', 'main')
    (path / 'tracked.txt').write_text('original\n')
    _git(path, 'add', 'tracked.txt')
    _git(path, 'commit', '-qm', 'initial')
    return path


def test_untracked_file_is_not_dirt(repo):
    """The regression: a stray file in the repo root must not stall the deploy."""
    (repo / '.bashrc').write_text('')
    assert _porcelain(repo, untracked=True) != ''  # what the old line saw
    assert _porcelain(repo, untracked=False) == ''  # what the watcher sees now


def test_modified_tracked_file_is_still_dirt(repo):
    """The protection that must survive: real in-progress work still blocks."""
    (repo / 'tracked.txt').write_text('edited\n')
    assert _porcelain(repo, untracked=False) != ''


def test_staged_change_is_still_dirt(repo):
    (repo / 'tracked.txt').write_text('edited\n')
    _git(repo, 'add', 'tracked.txt')
    assert _porcelain(repo, untracked=False) != ''


def test_staged_new_file_is_dirt_once_tracked(repo):
    """An untracked file becomes real work the moment it is `git add`ed."""
    (repo / 'new.txt').write_text('work\n')
    assert _porcelain(repo, untracked=False) == ''
    _git(repo, 'add', 'new.txt')
    assert _porcelain(repo, untracked=False) != ''


def test_deleted_tracked_file_is_dirt(repo):
    (repo / 'tracked.txt').unlink()
    assert _porcelain(repo, untracked=False) != ''
