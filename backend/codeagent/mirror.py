"""The coding agent's git: a mirror per repo, a clone per task, and the push.

Layout under AGENT_ROOT (default ./data/agent/):

    mirrors/<slug>.git      bare mirror of the GitHub remote, fetched per task
    tasks/<id>/work/        the agent's clone — mounted into the container
    tasks/<id>/home/        the agent's $HOME — mounted into the container
    tasks/<id>/sock/        the model proxy's Unix socket — mounted too
    tasks/<id>/out.git/     host-owned bare repo the agent's commits are
                            imported into; the diff and the push come from here

**The host never runs git inside `work/`.** The container can write all of it,
`.git/config` and `.git/hooks` included, and git executes things named there:
`core.fsmonitor` runs a command on `git status`, a `pre-push` hook runs on
push, `diff.external` on diff. One host-side `git diff` in the agent's clone is
a sandbox escape. So the agent's commits leave the container as a *bundle*,
written to `docker exec`'s stdout (so not even the bundle's path is the
agent's to choose), and are fetched into `out.git`, whose config only this
module ever writes. The bundle's prerequisite — the base commit — resolves
through `out.git`'s alternates link to the mirror, so nothing is copied.

The workspace is cloned *from the mirror on the host*, before the container
exists, with `--no-hardlinks` (shared inodes would let a chmod-and-write in the
container corrupt the mirror's objects) and then has its `origin` removed: the
container holds no remote URL and no credential.

The push is the one outbound write, and it is narrow on purpose. The refspec is
built here from the task id, never accepted from anywhere, and a branch name
that is not exactly `agent/<ULID>` is refused before git is invoked. The token
travels as `GIT_CONFIG_*` environment variables, so it is never in argv (where
`ps` shows it) or in any config file.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
from pathlib import Path

from backend.repos import git as repo_git
from backend.repos.git import GitError

_TASK_ID = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')
_AGENT_BRANCH = re.compile(r'^agent/[0-9A-HJKMNP-TV-Z]{26}$')
_GITHUB = re.compile(
    r'^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
    r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$'
)

# Identity for the agent's commits inside the container. The PR is opened by
# the machine account; this is just what `git log` shows.
AGENT_NAME = 'Lunaschal agent'
AGENT_EMAIL = 'agent@lunaschal.invalid'

# A bundle bigger than this is not a code change a phone can review.
MAX_BUNDLE_BYTES = 200 * 1024 * 1024
DIFF_LIMIT = 400_000  # characters of patch kept for the review screen


class PushRefused(ValueError):
    """The push would write somewhere other than the task's own agent branch."""


def agent_root() -> Path:
    """Re-read from the env on every call, so tests point it at tmp_path."""
    return Path(os.environ.get('AGENT_ROOT', './data/agent')).expanduser().resolve()


def is_task_id(task_id: str) -> bool:
    return bool(_TASK_ID.match(task_id or ''))


def branch_for(task_id: str) -> str:
    if not is_task_id(task_id):
        raise ValueError(f'not a task id: {task_id!r}')
    return f'agent/{task_id}'


def mirror_dir(slug: str) -> Path:
    from backend.repos.storage import is_safe_slug
    if not is_safe_slug(slug):
        raise ValueError(f'unsafe repo slug: {slug!r}')
    return agent_root() / 'mirrors' / f'{slug}.git'


def task_dir(task_id: str) -> Path:
    if not is_task_id(task_id):
        raise ValueError(f'not a task id: {task_id!r}')
    return agent_root() / 'tasks' / task_id


def work_dir(task_id: str) -> Path:
    return task_dir(task_id) / 'work'


def home_dir(task_id: str) -> Path:
    return task_dir(task_id) / 'home'


def sock_dir(task_id: str) -> Path:
    return task_dir(task_id) / 'sock'


def out_dir(task_id: str) -> Path:
    return task_dir(task_id) / 'out.git'


def github_repo(remote_url: str) -> tuple[str, str] | None:
    """(owner, repo) for a GitHub remote in any of its three spellings."""
    m = _GITHUB.match((remote_url or '').strip())
    return (m.group(1), m.group(2)) if m else None


def push_url(remote_url: str) -> str:
    """The https form of a GitHub remote. The machine account authenticates
    with a token, which only https carries — an SSH remote would push with the
    host user's own key, and the owner's key bypasses the `main` ruleset."""
    parsed = github_repo(remote_url)
    if not parsed:
        raise PushRefused('Pushing is only supported for github.com repositories')
    return f'https://github.com/{parsed[0]}/{parsed[1]}.git'


# ------------------------------------------------------------------ the mirror

def sync_mirror(slug: str, remote_url: str) -> Path:
    """Create the mirror, or bring it up to date. Returns its path."""
    dest = mirror_dir(slug)
    if (dest / 'HEAD').exists():
        repo_git._run(['fetch', '--quiet', '--prune', 'origin'], cwd=dest,
                      timeout=repo_git.CLONE_TIMEOUT)
        return dest
    repo_git.assert_clone_url(remote_url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    repo_git._run(['clone', '--quiet', '--mirror', '--', remote_url, str(dest)],
                  timeout=repo_git.CLONE_TIMEOUT)
    return dest


def default_branch(mirror: Path) -> str:
    ref = repo_git._run(['symbolic-ref', '--short', 'HEAD'], cwd=mirror)
    return ref or 'main'


# -------------------------------------------------------------- the workspace

def create_workspace(task_id: str, mirror: Path, base_branch: str = '',
                     start_point: str = '') -> str:
    """Clone the mirror into the task's work dir on `agent/<id>`, detached from
    any remote. Returns the base commit sha. Also creates `out.git`.

    `start_point` (a sha) starts the branch somewhere other than the tip of
    `base_branch` — the head-to-head harness replays historical fixes from
    the commit before each one."""
    mirror = Path(mirror).resolve()
    if agent_root() not in mirror.parents:
        # A local-path clone is exactly what assert_clone_url refuses, so the
        # path gets its own gate: only ever a mirror this module made.
        raise ValueError('workspace source must be a mirror under AGENT_ROOT')
    if start_point and not re.fullmatch(r'[0-9a-f]{7,40}', start_point):
        raise ValueError('start_point must be a commit sha')
    branch = branch_for(task_id)
    base_branch = base_branch or default_branch(mirror)
    root = task_dir(task_id)
    work = work_dir(task_id)
    if work.exists():
        raise FileExistsError(f'workspace already exists for {task_id}')
    for d in (home_dir(task_id), sock_dir(task_id)):
        d.mkdir(parents=True, exist_ok=True)
    repo_git._run(['clone', '--quiet', '--no-hardlinks', '--branch', base_branch,
                   '--', str(mirror), str(work)], timeout=repo_git.CLONE_TIMEOUT)
    repo_git._run(['checkout', '--quiet', '-b', branch, *([start_point] if start_point else [])],
                  cwd=work)
    base_sha = repo_git._run(['rev-parse', 'HEAD'], cwd=work)
    repo_git._run(['remote', 'remove', 'origin'], cwd=work)
    repo_git._run(['config', 'user.name', AGENT_NAME], cwd=work)
    repo_git._run(['config', 'user.email', AGENT_EMAIL], cwd=work)

    out = out_dir(task_id)
    repo_git._run(['init', '--quiet', '--bare', str(out)])
    (out / 'objects' / 'info').mkdir(parents=True, exist_ok=True)
    (out / 'objects' / 'info' / 'alternates').write_text(f'{mirror / "objects"}\n')
    (root / 'base').write_text(f'{base_branch}\n{base_sha}\n')
    return base_sha


def bundle_command(base_sha: str) -> list[str]:
    """What the driver runs *inside the container* to export the work. Writes
    the bundle to stdout; the host captures it into a file it chose."""
    if not re.fullmatch(r'[0-9a-f]{40}', base_sha or ''):
        raise ValueError('base sha must be a full hex sha')
    return ['git', '-C', '/work', 'bundle', 'create', '-', 'HEAD', f'^{base_sha}']


def import_bundle(task_id: str, bundle: Path) -> str:
    """Fetch the agent's commits from `bundle` into out.git's agent branch.
    Returns the imported head sha. Forced: the agent may have amended."""
    if bundle.stat().st_size > MAX_BUNDLE_BYTES:
        raise GitError('the bundle is too large to import')
    out = out_dir(task_id)
    branch = branch_for(task_id)
    repo_git._run(['bundle', 'verify', '--quiet', str(bundle)], cwd=out)
    repo_git._run(['fetch', '--quiet', '--force', str(bundle),
                   f'HEAD:refs/heads/{branch}'], cwd=out)
    return repo_git._run(['rev-parse', f'refs/heads/{branch}'], cwd=out)


def has_import(task_id: str) -> bool:
    try:
        repo_git._run(['rev-parse', '--verify', '--quiet',
                       f'refs/heads/{branch_for(task_id)}'], cwd=out_dir(task_id))
        return True
    except GitError:
        return False


def diff(task_id: str, base_sha: str) -> dict:
    """The review payload, from out.git — never from the agent's clone."""
    out = out_dir(task_id)
    head = f'refs/heads/{branch_for(task_id)}'
    rng = f'{base_sha}..{head}'
    files = []
    for line in repo_git._run(['diff', '--numstat', rng], cwd=out).splitlines():
        added, removed, path = line.split('\t', 2)
        files.append({
            'path': path,
            'added': int(added) if added.isdigit() else None,
            'removed': int(removed) if removed.isdigit() else None,
        })
    patch = repo_git._run(['diff', '--no-color', '--no-ext-diff', rng], cwd=out)
    commits = [c for c in repo_git._run(
        ['log', '--format=%h %s', rng], cwd=out).splitlines() if c]
    return {
        'files': files,
        'commits': commits,
        'patch': patch[:DIFF_LIMIT],
        'truncated': len(patch) > DIFF_LIMIT,
    }


# -------------------------------------------------------------------- the push

def push(task_id: str, remote_url: str, token: str, base_branch: str) -> str:
    """Push out.git's `agent/<id>` to GitHub as the machine account. Returns
    the branch name. Never anything else: the refspec is built right here."""
    branch = branch_for(task_id)
    if not _AGENT_BRANCH.match(branch):
        raise PushRefused(f'refusing to push {branch!r}')
    if branch == base_branch or base_branch.startswith('agent/'):
        raise PushRefused('the base branch cannot be an agent branch')
    if not token:
        raise PushRefused('No GitHub token is configured for the agent account')
    url = push_url(remote_url)
    repo_git.assert_clone_url(url)
    if not has_import(task_id):
        raise PushRefused('There is nothing to push yet')
    basic = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
    env = {
        'GIT_CONFIG_COUNT': '2',
        'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
        'GIT_CONFIG_VALUE_0': f'AUTHORIZATION: basic {basic}',
        # out.git is ours, but a hook here would still be a surprise.
        'GIT_CONFIG_KEY_1': 'core.hooksPath',
        'GIT_CONFIG_VALUE_1': os.devnull,
    }
    refspec = f'refs/heads/{branch}:refs/heads/{branch}'
    repo_git._run(['push', '--quiet', '--', url, refspec], cwd=out_dir(task_id),
                  timeout=repo_git.CLONE_TIMEOUT, extra_env=env)
    return branch


def delete_task(task_id: str) -> None:
    d = task_dir(task_id).resolve()
    if d.parent != agent_root() / 'tasks':
        return
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def free_bytes() -> int:
    root = agent_root()
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free
