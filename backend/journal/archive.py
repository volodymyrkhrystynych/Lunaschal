"""Where a journal video attachment's bytes live: the archive drive.

A YouTube video attached to a journal entry is the one journal attachment that
does not go under `./data/journal/` — it is hundreds of megabytes of something
already published elsewhere, so it goes to the same un-backed-up archive drive
Study's downloads and Piano's scores use, as a third collection beside them:

    <settings.backup_path>/archive/journal/<attachment_id>/video.<ext>

A sibling of Study's rather than a share of it. The ids are ULIDs so a literal
collision is not the worry; keeping the trees apart is what stops Study's
`delete_archived_dir` rmtree — which removes any direct child of *its* root —
from ever being pointed at a journal video by a future change.

The rule from backend/archive_location.py holds here: **an absent root is
unavailable, never something to create.** A mountpoint whose filesystem is not
mounted is an empty directory on the system SSD, and pouring a video into it
looks exactly like success until the disk fills.

The *thumbnail* deliberately does not live here — it stays on the SSD under
`./data/journal/<attachment_id>/thumb.jpg` (backend/journal/storage.py's
`thumb_path`). It is ~50 KB, it is backed up with everything else, and it means
the journal card still draws when the drive is unplugged. Only playback needs
the drive.
"""
import shutil
from pathlib import Path

from backend import archive_location
from backend.archive_location import ArchiveUnavailable
from backend.storage import is_safe_name

ARCHIVE_COLLECTION = 'journal'
ARCHIVE_ENV = 'JOURNAL_ARCHIVE_ROOT'

# What a downloaded video is allowed to be stored as. yt-dlp is asked to merge
# to mp4 (backend/ytdlp.py), but an unusual upload can fall through to whatever
# the last-resort `b` branch picked.
STORED_EXTS = {
    'mp4': 'video/mp4',
    'webm': 'video/webm',
    'mkv': 'video/x-matroska',
    'm4a': 'audio/mp4',
}


def archive_location_state(db=None):
    """Whether the archive drive is there, and why not if it isn't."""
    return archive_location.resolve(ARCHIVE_COLLECTION, ARCHIVE_ENV, db)


def archive_root(db=None, *, create: bool = False) -> Path:
    """The archive root, or `ArchiveUnavailable` explaining why there is none."""
    return archive_location.require_root(
        ARCHIVE_COLLECTION, ARCHIVE_ENV, db, create=create
    )


def video_dir(attachment_id: str, *, db=None, create: bool = False) -> Path | None:
    """`<archive root>/<attachment_id>/`, or None for an unsafe id.

    Raises `ArchiveUnavailable` when the drive is not there — never falls back
    to the SSD, which is the one failure mode that looks like success.
    """
    if not is_safe_name(attachment_id):
        return None
    return archive_root(db, create=create) / attachment_id


def resolve_archived_path(path_str: str, db=None) -> Path | None:
    """Only serve a path that is still a direct grandchild of the archive root
    (i.e. `<root>/<attachment_id>/<file>`), as defence in depth against a stored
    path that has since been tampered with.

    An unreachable drive makes the path unresolvable rather than unsafe — the
    caller 404s either way.
    """
    if not path_str:
        return None
    try:
        root = archive_root(db)
    except ArchiveUnavailable:
        return None
    if Path(path_str).parent.parent == root:
        return Path(path_str)
    return None


def delete_archived_dir(attachment_id: str, db=None) -> None:
    """Remove an attachment's directory from the archive, if the drive is there."""
    if not is_safe_name(attachment_id):
        return
    try:
        root = archive_root(db)
    except ArchiveUnavailable:
        return
    d = (root / attachment_id).resolve()
    # Belt and braces, matching IdScopedStorage.delete_dir: only ever delete a
    # direct child of the root.
    if d.parent != root.resolve() or not d.is_dir():
        return
    shutil.rmtree(d, ignore_errors=True)


def mimetype_for(path: str | Path) -> str:
    ext = Path(path).suffix.lower().lstrip('.')
    return STORED_EXTS.get(ext, 'application/octet-stream')
