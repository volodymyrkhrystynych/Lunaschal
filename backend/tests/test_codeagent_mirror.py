"""The coding agent's git plumbing, against real temporary repositories.

A local bare repo stands in for GitHub. `assert_clone_url` refuses local paths
by design, so — as test_repos_routes.py does — the tests step past the URL
check by monkeypatching it rather than loosening it. The test plays the
container's part by running git in the work dir directly, which is exactly the
thing the host code itself must never do.
"""
import subprocess
from pathlib import Path

import pytest
from ulid import ULID

from backend.codeagent import mirror
from backend.repos import git as repo_git

ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_COMMITTER_NAME': 'T',
       'GIT_COMMITTER_EMAIL': 't@e', 'PATH': '/usr/bin:/bin:/opt/homebrew/bin'}


def git(*args, cwd=None, **kw):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env={**ENV, 'HOME': str(cwd or '/tmp')}, **kw).stdout.strip()


@pytest.fixture
def github(tmp_path):
    """A bare repo playing GitHub, with one commit on main."""
    seed = tmp_path / 'seed'
    seed.mkdir()
    git('init', '-q', '-b', 'main', cwd=seed)
    (seed / 'a.py').write_text('x = 1\n')
    git('add', '-A', cwd=seed)
    git('commit', '-qm', 'first', cwd=seed)
    bare = tmp_path / 'github.git'
    git('clone', '-q', '--bare', str(seed), str(bare))
    return bare


@pytest.fixture(autouse=True)
def roots(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_ROOT', str(tmp_path / 'agent'))
    monkeypatch.setattr(repo_git, 'assert_clone_url', lambda url: url)


@pytest.fixture
def task_id():
    return str(ULID())


@pytest.fixture
def workspace(github, task_id):
    m = mirror.sync_mirror('demo', str(github))
    base = mirror.create_workspace(task_id, m)
    return task_id, base


def agent_commits(task_id, files):
    """What the agent does inside the container."""
    work = mirror.work_dir(task_id)
    for name, text in files.items():
        (work / name).write_text(text)
    git('add', '-A', cwd=work)
    git('commit', '-qm', 'agent change', cwd=work)


def export_and_import(task_id, base, tmp_path):
    cmd = mirror.bundle_command(base)
    # In production this runs via `docker exec`; here, against the work dir.
    cmd = [c if c != '/work' else str(mirror.work_dir(task_id)) for c in cmd]
    out = subprocess.run(cmd, capture_output=True, check=True, env=ENV).stdout
    bundle = tmp_path / 'host-chosen.bundle'
    bundle.write_bytes(out)
    return mirror.import_bundle(task_id, bundle)


def test_workspace_is_on_its_agent_branch_with_no_remote(workspace, github):
    task_id, base = workspace
    work = mirror.work_dir(task_id)
    assert git('rev-parse', '--abbrev-ref', 'HEAD', cwd=work) == f'agent/{task_id}'
    assert git('remote', cwd=work) == ''
    assert base == git('rev-parse', 'main', cwd=github)
    assert mirror.home_dir(task_id).is_dir() and mirror.sock_dir(task_id).is_dir()
    # Not hardlinked to the mirror: the container must not share its inodes.
    obj = next(p for p in (work / '.git' / 'objects').rglob('*') if p.is_file()
               and p.parent.name != 'info' and p.parent.name != 'pack')
    assert obj.stat().st_nlink == 1


def test_mirror_is_refreshed_on_the_second_sync(github, tmp_path):
    m = mirror.sync_mirror('demo', str(github))
    seed = tmp_path / 'seed'
    (seed / 'b.py').write_text('y = 2\n')
    git('add', '-A', cwd=seed)
    git('commit', '-qm', 'second', cwd=seed)
    git('push', '-q', str(github), 'main', cwd=seed)
    mirror.sync_mirror('demo', str(github))
    assert git('rev-parse', 'main', cwd=m) == git('rev-parse', 'main', cwd=github)


def test_commits_come_out_as_a_bundle_and_diff_from_out_git(workspace, tmp_path):
    task_id, base = workspace
    agent_commits(task_id, {'a.py': 'x = 2\n', 'new.py': 'print(1)\n'})
    head = export_and_import(task_id, base, tmp_path)
    assert head == git('rev-parse', 'HEAD', cwd=mirror.work_dir(task_id))
    d = mirror.diff(task_id, base)
    assert {f['path'] for f in d['files']} == {'a.py', 'new.py'}
    assert '+x = 2' in d['patch'] and not d['truncated']
    assert d['commits'][0].endswith('agent change')


def test_the_hosts_diff_ignores_config_the_agent_planted(workspace, tmp_path):
    task_id, base = workspace
    work = mirror.work_dir(task_id)
    marker = tmp_path / 'pwned'
    # Both of these would execute on the host if it ran git in the work dir.
    git('config', 'core.fsmonitor', f'touch {marker}; false', cwd=work)
    hook = work / '.git' / 'hooks' / 'pre-push'
    hook.write_text(f'#!/bin/sh\ntouch {marker}\n')
    hook.chmod(0o755)
    git('config', '--unset', 'core.fsmonitor', cwd=work)  # let the "container" commit
    agent_commits(task_id, {'a.py': 'x = 3\n'})
    git('config', 'core.fsmonitor', f'touch {marker}; false', cwd=work)
    export_and_import(task_id, base, tmp_path)
    mirror.diff(task_id, base)
    assert not marker.exists()


def test_push_writes_exactly_the_agent_branch(workspace, github, tmp_path, monkeypatch):
    task_id, base = workspace
    agent_commits(task_id, {'a.py': 'x = 4\n'})
    export_and_import(task_id, base, tmp_path)
    monkeypatch.setattr(mirror, 'push_url', lambda url: str(github))
    main_before = git('rev-parse', 'main', cwd=github)

    branch = mirror.push(task_id, 'https://github.com/o/r', 'tok', 'main')

    assert branch == f'agent/{task_id}'
    heads = git('for-each-ref', '--format=%(refname)', 'refs/heads', cwd=github).splitlines()
    assert sorted(heads) == sorted(['refs/heads/main', f'refs/heads/agent/{task_id}'])
    assert git('rev-parse', 'main', cwd=github) == main_before


def test_push_refuses_without_a_token_or_an_import(workspace):
    task_id, _ = workspace
    with pytest.raises(mirror.PushRefused, match='token'):
        mirror.push(task_id, 'https://github.com/o/r', '', 'main')
    with pytest.raises(mirror.PushRefused, match='nothing to push'):
        mirror.push(task_id, 'https://github.com/o/r', 'tok', 'main')


def test_push_refuses_a_non_github_remote(workspace):
    task_id, _ = workspace
    with pytest.raises(mirror.PushRefused, match='github.com'):
        mirror.push(task_id, 'https://gitlab.com/o/r', 'tok', 'main')


@pytest.mark.parametrize('bad', ['', 'main', '../x', '01HZZZZZZZZZZZZZZZZZZZZZZZ/../../x', 'agent/x'])
def test_task_ids_are_validated(bad):
    with pytest.raises(ValueError):
        mirror.branch_for(bad)
    with pytest.raises(ValueError):
        mirror.task_dir(bad)


@pytest.mark.parametrize('url, expected', [
    ('https://github.com/me/Lunaschal', ('me', 'Lunaschal')),
    ('https://github.com/me/Lunaschal.git', ('me', 'Lunaschal')),
    ('git@github.com:me/Lunaschal.git', ('me', 'Lunaschal')),
    ('ssh://git@github.com/me/Lunaschal.git', ('me', 'Lunaschal')),
    ('https://gitlab.com/me/x', None),
    ('https://github.com/me', None),
])
def test_github_repo(url, expected):
    assert mirror.github_repo(url) == expected


def test_push_url_is_always_https():
    assert mirror.push_url('git@github.com:me/r.git') == 'https://github.com/me/r.git'


def test_workspace_source_must_be_our_mirror(task_id, github):
    with pytest.raises(ValueError, match='mirror under AGENT_ROOT'):
        mirror.create_workspace(task_id, Path(github))


def test_bundle_command_needs_a_real_sha():
    with pytest.raises(ValueError):
        mirror.bundle_command('main; rm -rf /')


def test_delete_task_removes_only_the_task(workspace):
    task_id, _ = workspace
    mirror.delete_task(task_id)
    assert not mirror.task_dir(task_id).exists()
    assert mirror.mirror_dir('demo').exists()
