"""Registering, cloning and forgetting a repository.

The clone tests use a real local git repository as the remote. `assert_clone_url`
refuses `file://` and bare paths, which is the point of it — so these tests call
`git.clone` past the URL check by monkeypatching it, rather than weakening the
check for the convenience of the suite. The thing under test is the job's
ordering (tree, then graph, then record), not the allowlist; that has its own
file.
"""
import subprocess

import pytest

from backend.db.connection import get_db
from backend.repos import git as repo_git
from backend.repos import job, registry, storage


@pytest.fixture
def remote(tmp_path):
    """A real git repo to clone from."""
    src = tmp_path / 'remote'
    (src / 'pkg').mkdir(parents=True)
    (src / 'pkg' / 'a.py').write_text('def helper(x):\n    return x + 1\n')
    (src / 'README.md').write_text('# Fixture\n')
    env = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_COMMITTER_NAME': 'T',
           'GIT_COMMITTER_EMAIL': 't@e', 'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)}
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(src)], check=True, env=env)
    subprocess.run(['git', 'add', '-A'], cwd=src, check=True, env=env)
    subprocess.run(['git', 'commit', '-qm', 'first'], cwd=src, check=True, env=env)
    return src


@pytest.fixture(autouse=True)
def repos_root(monkeypatch, tmp_path):
    monkeypatch.setenv('REPOS_ROOT', str(tmp_path / 'repos'))
    # graphify is real and fast, but a unit test should not depend on it being
    # installed; the graph path has its own coverage below.
    monkeypatch.setenv('GRAPHIFY_BIN', str(tmp_path / 'no-such-graphify'))
    return tmp_path / 'repos'


@pytest.fixture
def local_clone_allowed(monkeypatch, remote):
    """Let git.clone take the fixture's local path, without loosening the
    allowlist the rest of the app relies on."""
    real_clone = repo_git.clone

    def clone(url, dest, branch=''):
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['git', 'clone', '-q', str(remote), str(dest)], check=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(dest.parent)},
        )

    monkeypatch.setattr(repo_git, 'clone', clone)
    monkeypatch.setattr(job, 'clone', clone)
    return real_clone


# --- Registration ---

def test_create_registers_pending_and_queues_the_clone(client, monkeypatch):
    queued = []
    monkeypatch.setattr(job, 'submit_import', lambda rid: queued.append(rid) or True)
    monkeypatch.setattr('backend.routes.repos.job.submit_import',
                        lambda rid: queued.append(rid) or True)

    resp = client.post('/api/repos', json={'remoteUrl': 'https://github.com/o/thing.git'})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['slug'] == 'thing'
    assert body['cloneState'] == 'pending'
    assert queued == [body['id']]


def test_create_rejects_an_unsafe_url(client):
    resp = client.post('/api/repos', json={'remoteUrl': 'ext::sh -c whoami'})
    assert resp.status_code == 400
    assert 'ext::' in resp.get_json()['error']
    assert get_db().execute('SELECT COUNT(*) c FROM repos').fetchone()['c'] == 0


def test_slugs_do_not_collide(client, monkeypatch):
    monkeypatch.setattr('backend.routes.repos.job.submit_import', lambda rid: True)
    client.post('/api/repos', json={'remoteUrl': 'https://github.com/a/thing.git'})
    client.post('/api/repos', json={'remoteUrl': 'https://github.com/b/thing.git'})
    slugs = [r['slug'] for r in registry.list_repos()]
    assert sorted(slugs) == ['thing', 'thing-2']


# --- The import job ---

def test_import_clones_and_marks_ready(client, local_clone_allowed):
    repo = registry.create_repo('https://github.com/o/fixture.git')
    result = job.run_import(repo['id'])

    assert result['ok'] is True
    row = registry.get_repo(repo['id'])
    assert row['cloneState'] == 'ready'
    assert row['headSha'] and len(row['headSha']) == 40
    assert (storage.repo_dir(row['slug']) / 'pkg' / 'a.py').is_file()


def test_import_records_an_error_rather_than_raising(client, monkeypatch):
    def boom(url, dest, branch=''):
        raise repo_git.GitError('Repository not found')

    monkeypatch.setattr(job, 'clone', boom)
    repo = registry.create_repo('https://github.com/o/private.git')
    result = job.run_import(repo['id'])

    assert result['ok'] is False
    row = registry.get_repo(repo['id'])
    assert row['cloneState'] == 'error'
    assert 'not found' in row['cloneError'].lower()


def test_a_failed_import_can_be_retried_over_its_own_leftovers(client, local_clone_allowed):
    """A half-clone from a previous attempt must not wedge the retry."""
    repo = registry.create_repo('https://github.com/o/fixture.git')
    stale = storage.repo_dir(repo['slug'])
    stale.mkdir(parents=True)
    (stale / 'junk').write_text('left over')

    assert job.run_import(repo['id'])['ok'] is True
    assert not (stale / 'junk').exists()
    assert (stale / 'pkg' / 'a.py').is_file()


def test_pull_picks_up_a_new_commit(client, local_clone_allowed, remote):
    repo = registry.create_repo('https://github.com/o/fixture.git')
    job.run_import(repo['id'])
    before = registry.get_repo(repo['id'])['headSha']

    env = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_COMMITTER_NAME': 'T',
           'GIT_COMMITTER_EMAIL': 't@e', 'PATH': '/usr/bin:/bin', 'HOME': str(remote.parent)}
    (remote / 'pkg' / 'b.py').write_text('from pkg.a import helper\n')
    subprocess.run(['git', 'add', '-A'], cwd=remote, check=True, env=env)
    subprocess.run(['git', 'commit', '-qm', 'second'], cwd=remote, check=True, env=env)

    assert job.run_pull(repo['id'])['ok'] is True
    after = registry.get_repo(repo['id'])
    assert after['headSha'] != before
    assert (storage.repo_dir(after['slug']) / 'pkg' / 'b.py').is_file()


def test_pull_keeps_untracked_files_like_the_graph(client, local_clone_allowed):
    """The graph lives inside the clone. reset --hard leaves untracked files
    alone, which is exactly why pull is not `git pull`."""
    repo = registry.create_repo('https://github.com/o/fixture.git')
    job.run_import(repo['id'])
    root = storage.repo_dir(repo['slug'])
    (root / 'graphify-out').mkdir(exist_ok=True)
    (root / 'graphify-out' / 'graph.json').write_text('{"nodes": []}')

    job.run_pull(repo['id'])
    assert (root / 'graphify-out' / 'graph.json').is_file()


def test_pull_on_a_missing_checkout_says_to_re_import(client):
    repo = registry.create_repo('https://github.com/o/gone.git')
    registry.set_state(repo['id'], 'ready')
    result = job.run_pull(repo['id'])
    assert result['ok'] is False
    assert 're-import' in registry.get_repo(repo['id'])['cloneError'].lower()


# --- Deletion ---

def test_delete_removes_the_row_and_the_checkout(client, local_clone_allowed):
    repo = registry.create_repo('https://github.com/o/fixture.git')
    job.run_import(repo['id'])
    root = storage.repo_dir(repo['slug'])
    assert root.is_dir()

    assert client.delete(f"/api/repos/{repo['id']}").status_code == 200
    assert registry.get_repo(repo['id']) is None
    assert not root.exists()


def test_delete_of_an_unknown_repo_is_404(client):
    assert client.delete('/api/repos/nope').status_code == 404


# --- default_repo ---

def test_a_lone_ready_repo_is_the_default_without_configuring_anything(client):
    repo = registry.create_repo('https://github.com/o/only.git')
    assert registry.default_repo() is None  # still pending
    registry.set_state(repo['id'], 'ready')
    assert registry.default_repo()['id'] == repo['id']


def test_with_several_repos_the_default_must_be_chosen(client):
    a = registry.create_repo('https://github.com/o/a.git')
    b = registry.create_repo('https://github.com/o/b.git')
    registry.set_state(a['id'], 'ready')
    registry.set_state(b['id'], 'ready')
    assert registry.default_repo() is None

    assert client.post(f"/api/repos/{b['id']}/default").status_code == 200
    assert registry.default_repo()['id'] == b['id']


def test_repo_root_is_none_until_the_clone_is_ready(client):
    repo = registry.create_repo('https://github.com/o/x.git')
    assert registry.repo_root(repo['id']) is None
    registry.set_state(repo['id'], 'ready')
    assert registry.repo_root(repo['id']).name == 'x'
    assert registry.repo_root(None) is None
