"""Where a registered repository's checkout lives on disk.

One directory per repo under ./data/repos/<slug>/ (REPOS_ROOT), the same
env-overridable layout as FANFIC_ROOT/MEETINGS_ROOT and friends. Unlike those,
the directory name is the repo's *slug* rather than its ULID: these are things
the user will `cd` into by hand when something looks wrong, and
`data/repos/lunaschal` is findable in a way `data/repos/01J8...` is not.

`resolve_within` is the single path gate every code tool goes through
(backend/research/code.py). It is here rather than there because the rule it
enforces — "this path is inside this clone" — is a fact about the storage
layout, and one copy of it is the only way it stays true.
"""
import os
import re
import shutil
from pathlib import Path, PurePosixPath

# Slugs name a directory, so they get the strictest of the app's name rules:
# lowercase, no dots at all (which rules out '.' and '..' without a special
# case), and bounded so a pathological remote URL cannot produce a name the
# filesystem rejects.
_SAFE_SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')

# Never handed to the model, never listed, never read. .git is excluded because
# a packed object file is not source and reading one wastes a turn; the rest are
# excluded because an article that quotes a credential leaks it into every
# prompt that article is ever retrieved into.
SECRET_PATTERNS = (
    '.git/*', '.git', '*.pem', '*.key', '*.p12', '*.pfx',
    'id_rsa*', 'id_ed25519*', '*.keystore', '*.jks',
)
# Matched against the file *name* rather than the path, so data/.env.local and
# .env are both caught.
SECRET_NAME_PREFIXES = ('.env',)


def is_safe_slug(slug: str) -> bool:
    return bool(_SAFE_SLUG.match(slug or ''))


def repos_root() -> Path:
    """Re-read from the env on every call, not cached, so tests can point it
    at a tmp_path per case — the IdScopedStorage convention."""
    return Path(os.environ.get('REPOS_ROOT', './data/repos')).expanduser().resolve()


def repo_dir(slug: str) -> Path | None:
    if not is_safe_slug(slug):
        return None
    return repos_root() / slug


def delete_repo_dir(slug: str) -> None:
    d = repo_dir(slug)
    if d is None:
        return
    # Belt and braces: only ever remove a direct child of the root, even if the
    # slug somehow got past is_safe_slug.
    d = d.resolve()
    if d.parent != repos_root():
        return
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def is_secret_path(rel: str) -> bool:
    """True for paths that must never be read, listed or searched.

    Takes the *relative* path so the check is about the repo's own layout and
    not about where the clone happens to sit.
    """
    path = PurePosixPath(rel)
    for part in path.parts:
        if any(part.startswith(prefix) for prefix in SECRET_NAME_PREFIXES):
            return True
    if '.git' in path.parts:
        return True
    name = path.name
    for pattern in SECRET_PATTERNS:
        if pattern in ('.git', '.git/*'):
            continue
        if path.match(pattern) or PurePosixPath(name).match(pattern):
            return True
    return False


def resolve_within(root: Path, rel: str) -> Path | None:
    """`root/rel` if it really is inside `root`, else None.

    Both sides are resolved before comparing, so a symlink inside the clone
    pointing at /etc is refused the same as a literal '../..'. A repo is a
    checkout of someone else's code and may legitimately contain symlinks; the
    guarantee is about what we hand back, not about what is on disk.
    """
    rel = (rel or '').strip().lstrip('/')
    if is_secret_path(rel):
        return None
    try:
        root = root.resolve()
        target = (root / rel).resolve()
    except (OSError, RuntimeError):  # RuntimeError: symlink loop
        return None
    if target != root and root not in target.parents:
        return None
    return target
