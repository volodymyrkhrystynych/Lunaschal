"""Thin, testable wrapper around the `git` CLI for syncing the Files folder.

The Files tab's FILES_ROOT is treated as a git working tree. This module drives
commit / pull / push against a self-hosted SSH remote so the user can manually
sync notes between machines. No Flask imports here — every function takes an
explicit `root: Path` so it can be unit-tested against a temp dir + bare repo.
"""
from __future__ import annotations

import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BRANCH = 'main'

# .trash/ is the soft-delete bin (see routes/files.py) — never sync it.
GITIGNORE_BODY = """\
.trash/
.obsidian/
*.tmp
"""


def _git_env() -> dict:
    """Environment that makes git fail fast instead of hanging.

    - GIT_TERMINAL_PROMPT=0: never block on an interactive credential prompt.
    - GIT_SSH_COMMAND with BatchMode: SSH key/agent only; offline or unauthorized
      pushes error out immediately rather than waiting for a password prompt.
    """
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.setdefault(
        'GIT_SSH_COMMAND',
        'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new',
    )
    return env


def _run(args: list[str], root: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
    )


def is_repo(root: Path) -> bool:
    return (Path(root) / '.git').is_dir()


def write_gitignore(root: Path) -> None:
    """Create .gitignore if absent (don't clobber a user-edited one)."""
    path = Path(root) / '.gitignore'
    if not path.exists():
        path.write_text(GITIGNORE_BODY, encoding='utf-8')


def _ensure_identity(root: Path) -> None:
    """Set a local commit identity if none is configured, so commits don't fail."""
    if not _run(['config', 'user.email'], root).stdout.strip():
        _run(['config', 'user.email', 'lunaschal@localhost'], root)
    if not _run(['config', 'user.name'], root).stdout.strip():
        _run(['config', 'user.name', f'Lunaschal {socket.gethostname()}'], root)


def init(root: Path, branch: str = DEFAULT_BRANCH) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # `git init -b` sets the initial branch; fall back for very old git.
    res = _run(['init', '-b', branch], root)
    if res.returncode != 0:
        _run(['init'], root)
        _run(['symbolic-ref', 'HEAD', f'refs/heads/{branch}'], root)
    _ensure_identity(root)
    write_gitignore(root)


def clone(remote_url: str, root: Path, branch: str = DEFAULT_BRANCH) -> dict:
    """Clone a populated remote into an empty/absent `root` (onboarding a machine).

    Two independently-initialized repos have unrelated histories that refuse to
    merge, so a second machine must clone rather than init+pull.
    """
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        return {'ok': False, 'error': 'Notes folder is not empty — cannot clone into it'}
    res = subprocess.run(
        ['git', 'clone', '--branch', branch, remote_url, str(root)],
        capture_output=True, text=True, timeout=300, env=_git_env(),
    )
    if res.returncode != 0:
        return {'ok': False, 'error': (res.stderr or res.stdout).strip() or 'git clone failed'}
    _ensure_identity(root)
    return {'ok': True}


def remote_has_content(remote_url: str, timeout: int = 30) -> bool | None:
    """True/False if the remote has any branches; None if unreachable."""
    res = subprocess.run(
        ['git', 'ls-remote', '--heads', remote_url],
        capture_output=True, text=True, timeout=timeout, env=_git_env(),
    )
    if res.returncode != 0:
        return None
    return bool(res.stdout.strip())


def _remote_has_branch(root: Path, branch: str) -> bool:
    return bool(_run(['ls-remote', '--heads', 'origin', branch], root).stdout.strip())


def get_remote(root: Path) -> str | None:
    res = _run(['remote', 'get-url', 'origin'], root)
    return res.stdout.strip() if res.returncode == 0 else None


def set_remote(root: Path, url: str) -> None:
    if get_remote(root) is None:
        _run(['remote', 'add', 'origin', url], root)
    else:
        _run(['remote', 'set-url', 'origin', url], root)


def current_branch(root: Path) -> str:
    """Return the checked-out branch (works on an unborn branch), or 'HEAD' when detached.

    `symbolic-ref` reports the branch name even before the first commit; it only
    fails when HEAD is genuinely detached.
    """
    res = _run(['symbolic-ref', '--short', '-q', 'HEAD'], root)
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else 'HEAD'


def _has_conflicts(root: Path) -> bool:
    return bool(_run(['ls-files', '-u'], root).stdout.strip())


def status(root: Path) -> dict:
    """Working-tree status: dirty / conflicted / ahead / behind vs upstream."""
    root = Path(root)
    dirty = bool(_run(['status', '--porcelain'], root).stdout.strip())
    conflicted = _has_conflicts(root)
    detached = current_branch(root) == 'HEAD'

    ahead: int | None = None
    behind: int | None = None
    has_upstream = False
    # left = upstream-only (behind), right = local-only (ahead)
    rev = _run(['rev-list', '--left-right', '--count', '@{u}...HEAD'], root)
    if rev.returncode == 0 and rev.stdout.strip():
        parts = rev.stdout.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
            has_upstream = True

    return {
        'dirty': dirty,
        'conflicted': conflicted,
        'detached': detached,
        'ahead': ahead,
        'behind': behind,
        'has_upstream': has_upstream,
    }


def commit_all(root: Path, message: str | None = None) -> bool:
    """Stage everything and commit. Returns True if a commit was created.

    'nothing to commit' is treated as a no-op success (returns False).
    """
    root = Path(root)
    _run(['add', '-A'], root)
    if message is None:
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        message = f'{socket.gethostname()} sync {stamp}'
    res = _run(['commit', '-m', message], root)
    if res.returncode == 0:
        return True
    combined = (res.stdout + res.stderr).lower()
    if 'nothing to commit' in combined or 'no changes added' in combined:
        return False
    # Real failure (e.g. missing identity) — surface it.
    raise RuntimeError((res.stderr or res.stdout).strip() or 'git commit failed')


def pull(root: Path, branch: str) -> dict:
    """Merge-pull from origin. Reports conflicts as a first-class state."""
    res = _run(['pull', '--no-rebase', 'origin', branch], root)
    if res.returncode == 0:
        return {'ok': True, 'conflicted': False, 'output': res.stdout}
    output = res.stdout + res.stderr
    if 'CONFLICT' in output or _has_conflicts(Path(root)):
        return {'ok': False, 'conflicted': True, 'error': 'Merge conflict', 'output': output}
    return {'ok': False, 'conflicted': False, 'error': (res.stderr or res.stdout).strip(), 'output': output}


def push(root: Path, branch: str, set_upstream: bool = False) -> dict:
    args = ['push']
    if set_upstream:
        args.append('-u')
    args += ['origin', branch]
    res = _run(args, root)
    if res.returncode == 0:
        return {'ok': True, 'output': res.stdout + res.stderr}
    return {'ok': False, 'error': (res.stderr or res.stdout).strip(), 'output': res.stdout + res.stderr}


def sync(root: Path, branch: str) -> dict:
    """commit -> pull -> push. Stops on conflict, leaving markers for the user."""
    root = Path(root)
    if current_branch(root) == 'HEAD':
        return {'ok': False, 'phase': 'precheck', 'error': 'Detached HEAD — check out a branch before syncing'}

    try:
        commit_all(root)
    except RuntimeError as e:
        return {'ok': False, 'phase': 'commit', 'error': str(e)}

    st = status(root)
    # Skip the pull when the remote branch doesn't exist yet (first push to an
    # empty remote) — there's nothing to merge and `git pull` would error.
    if _remote_has_branch(root, branch):
        pull_res = pull(root, branch)
        if not pull_res['ok']:
            if pull_res.get('conflicted'):
                return {'ok': False, 'phase': 'pull', 'conflicted': True,
                        'error': 'Merge conflict — resolve the markers in the affected files, then Sync again'}
            return {'ok': False, 'phase': 'pull', 'error': pull_res.get('error') or 'Pull failed'}

    push_res = push(root, branch, set_upstream=not st['has_upstream'])
    if not push_res['ok']:
        return {'ok': False, 'phase': 'push', 'error': push_res.get('error') or 'Push failed'}

    return {'ok': True}
