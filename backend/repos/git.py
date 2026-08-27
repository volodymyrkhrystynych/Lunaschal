"""Git operations on a registered repository's clone.

**The URL check here is not backend/research/web.py's check, and confusing the
two would get both wrong.** `web.py` guards against SSRF because *the model*
picks the URLs it fetches. Here the user types the URL, so the threat is not
where the request goes — it is what `git` will do with it. Git's transport list
includes `ext::`, which runs an arbitrary shell command *by design*, and
`file://`, which would clone a path off this machine. An argument beginning with
`-` is read as a flag, and `--upload-pack=...` on a clone is remote code
execution on this side. So the allowlist is: https, and the two SSH spellings.

Nothing here stores a credential. An SSH URL uses whatever the user's agent and
~/.ssh already offer; an https URL to a private repo will simply fail, and it
must fail *fast* — GIT_TERMINAL_PROMPT=0 plus an empty GIT_ASKPASS is what stops
git blocking forever on a username prompt no one will ever see.
"""
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CLONE_TIMEOUT = 900   # 15 min: a large repo over a slow link, cloned once.
GIT_TIMEOUT = 120     # everything else — fetch, reset, rev-parse.

_HTTPS = re.compile(r'^https://[A-Za-z0-9._~-]+(:\d+)?/[^\s]+$')
# git@host:owner/repo.git — scp-like syntax, the form GitHub hands out.
_SCP = re.compile(r'^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s/][^\s]*$')
_SSH = re.compile(r'^ssh://([A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+(:\d+)?/[^\s]+$')

_SLUG_STRIP = re.compile(r'[^a-z0-9_-]+')


class UnsafeRemote(ValueError):
    """A remote URL we will not hand to git."""


class GitError(RuntimeError):
    """A git command failed. The message is git's own stderr, trimmed."""


def assert_clone_url(url: str) -> str:
    """Return the URL if git may be pointed at it, else raise UnsafeRemote.

    Pure — no network, no filesystem — so the whole policy is unit-testable.
    """
    url = (url or '').strip()
    if not url:
        raise UnsafeRemote('No URL given')
    if url.startswith('-'):
        # git would read this as an option, and --upload-pack=<cmd> executes it.
        raise UnsafeRemote('A remote URL may not start with "-"')
    if any(c in url for c in '\n\r\x00'):
        raise UnsafeRemote('A remote URL may not contain control characters')
    lowered = url.lower()
    if lowered.startswith('ext::'):
        raise UnsafeRemote('The ext:: transport runs arbitrary commands and is refused')
    if lowered.startswith(('file://', 'git://')):
        raise UnsafeRemote(
            'Only https:// and ssh remotes are accepted — '
            'file:// clones local paths and git:// is unauthenticated'
        )
    if _HTTPS.match(url) or _SCP.match(url) or _SSH.match(url):
        return url
    raise UnsafeRemote(
        'Expected an https://host/owner/repo or git@host:owner/repo URL'
    )


def slug_for_url(url: str) -> str:
    """A directory-safe name derived from the last path segment.

    Collisions are the registry's problem (it suffixes); this only has to be
    deterministic and safe.
    """
    tail = re.split(r'[:/]', (url or '').rstrip('/'))[-1]
    tail = tail[:-4] if tail.endswith('.git') else tail
    slug = _SLUG_STRIP.sub('-', tail.lower()).strip('-')
    return slug[:64] or 'repo'


def _env() -> dict:
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_ASKPASS'] = ''
    env['SSH_ASKPASS'] = ''
    # A repo's own hooks must never run here: we clone and read, we never build.
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    return env


def _run(args: list[str], cwd: Path | None = None, timeout: int = GIT_TIMEOUT) -> str:
    try:
        out = subprocess.run(
            ['git', *args], cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, timeout=timeout, env=_env(),
        )
    except subprocess.TimeoutExpired:
        raise GitError(f'git {args[0]} timed out after {timeout}s')
    except OSError as e:
        raise GitError(f'Could not run git: {e}')
    if out.returncode != 0:
        stderr = (out.stderr or out.stdout or '').strip()
        raise GitError(stderr.splitlines()[-1] if stderr else f'git {args[0]} failed')
    return out.stdout.strip()


def clone(url: str, dest: Path, branch: str = '') -> None:
    """Clone `url` into `dest`. The URL is re-checked here, not just at the
    route: this is the function that hands it to git, so this is where the
    guarantee has to hold."""
    assert_clone_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ['clone', '--quiet']
    if branch:
        args += ['--branch', branch]
    # `--` so a URL that somehow got past the check still cannot be read as a
    # flag. Belt and braces on top of assert_clone_url, not instead of it.
    args += ['--', url, str(dest)]
    _run(args, timeout=CLONE_TIMEOUT)


def current_branch(root: Path) -> str:
    return _run(['rev-parse', '--abbrev-ref', 'HEAD'], cwd=root)


def head_sha(root: Path) -> str:
    return _run(['rev-parse', 'HEAD'], cwd=root)


def pull(root: Path, branch: str = '') -> str:
    """Fetch and hard-reset onto the remote branch. Returns the new HEAD sha.

    `reset --hard` rather than `git pull` on purpose. The clone carries an
    untracked graphify-out/ and may carry whatever else a tool left behind, and
    a merge-based pull is one conflict away from wedging a checkout no human is
    watching. Reset does not touch untracked files, so the graph survives; and
    since nothing ever writes into a clone, there is no local work to lose.
    """
    _run(['fetch', '--quiet', '--prune', 'origin'], cwd=root)
    branch = branch or current_branch(root)
    _run(['reset', '--hard', '--quiet', f'origin/{branch}'], cwd=root)
    return head_sha(root)


def is_clone(root: Path | None) -> bool:
    return bool(root) and (root / '.git').exists()


def changed_files(root: Path, since_sha: str) -> list[str]:
    """Paths touched between `since_sha` and HEAD, or [] when the range is
    unusable (a rebase or a branch switch makes the old sha unreachable)."""
    if not since_sha:
        return []
    try:
        out = _run(['diff', '--name-only', f'{since_sha}..HEAD'], cwd=root)
    except GitError:
        return []
    return [line for line in out.splitlines() if line]
