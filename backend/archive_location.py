"""Where a collection's large files live on the external archive drive.

`<settings.backup_path>/archive/<collection>/`, beside the `media/` tree the
nightly rsync writes — a *sibling* of the backup, not part of it. Files here
are the only copy: they are archived rather than backed up, because the things
that go here (a 250,000-score library, a downloaded lecture) are either too
large to mirror or one download away from being replaced.

**An absent root is unavailable, never something to create.** The target is a
removable drive, and a mountpoint whose filesystem is not mounted is just an
empty directory on the system SSD — `mkdir -p` onto it followed by a write
would quietly pour the archive into the root partition and look like it worked.
`backend/email/media.py` says the same thing about the mail archive; this module
is the shared version of the resolution both Piano and Study need, extracted
rather than copied, for the reason `backend/study/CLAUDE.md` gives about the
SSRF fetch loop: a second copy is how a guard goes missing from one of them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from backend.ops.backup_config import get_config


class ArchiveUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveLocation:
    root: Path | None
    configured: bool
    available: bool
    writable: bool
    destination: str | None
    reason: str | None


def resolve(collection: str, env_var: str, db=None) -> ArchiveLocation:
    """The archive root for `collection`, from `env_var` or the backup path."""
    override = os.environ.get(env_var, '').strip()
    if override:
        root = Path(override).expanduser().resolve()
        # Probe the parent when the root itself has never been created: an
        # override naming a directory we would create on first write is still
        # available, as long as something above it exists.
        probe = root if root.exists() else root.parent
        available = probe.is_dir()
        writable = available and os.access(probe, os.W_OK)
        return ArchiveLocation(
            root=root,
            configured=True,
            available=available,
            writable=writable,
            destination=str(root),
            reason=None if available else 'The archive folder is unavailable.',
        )

    from backend.db.connection import get_db

    cfg = get_config(db or get_db())
    destination = cfg['path'].strip()
    if not destination:
        return ArchiveLocation(
            root=None,
            configured=False,
            available=False,
            writable=False,
            destination=None,
            reason='Choose the main backup folder in Settings first.',
        )
    base = Path(destination).expanduser()
    if not base.is_dir():
        return ArchiveLocation(
            root=(base / 'archive' / collection),
            configured=True,
            available=False,
            writable=False,
            destination=destination,
            reason='The backup drive is not connected.',
        )
    writable = os.access(base, os.W_OK)
    return ArchiveLocation(
        root=(base / 'archive' / collection).resolve(),
        configured=True,
        available=True,
        writable=writable,
        destination=destination,
        reason=None if writable else 'The backup drive is not writable.',
    )


def require_root(
    collection: str, env_var: str, db=None, *, create: bool = False
) -> Path:
    """The root, or `ArchiveUnavailable` explaining why there isn't one."""
    state = resolve(collection, env_var, db)
    if not state.available or state.root is None:
        raise ArchiveUnavailable(state.reason or 'The archive is unavailable.')
    if create and not state.writable:
        raise ArchiveUnavailable(state.reason or 'The archive is not writable.')
    if create:
        state.root.mkdir(parents=True, exist_ok=True)
    return state.root
