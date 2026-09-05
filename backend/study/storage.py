"""File storage for study sources — two roots, split by size, not by kind.

PDFs and archived web pages go to `<STUDY_ROOT>/<source_id>/` on the system
SSD, the same `<root>/<id>/…` layout (and the same `IdScopedStorage`) as
fanfic, meetings, food and paper, and they ride the nightly backup like
everything else under `data/`. An uploaded PDF has no source URL: if it is lost
it is gone, and it is a few megabytes.

Downloaded videos go to the **archive** instead —
`<settings.backup_path>/archive/study/<source_id>/`, beside `archive/piano` —
and they live there and nowhere else. A lecture is hundreds of megabytes and is
one `yt-dlp` away from being replaced, so mirroring it into `data/` would mean
the backup carrying three copies of a file nobody would restore. The archive is
a sibling of the rsync destination, not inside it, so this needs no
`ops/backup.sh` change: moving the bytes out of `data/` removes them from both
the drive mirror and the tablet copy.

The consequence is deliberate and permanent: **with the drive unplugged, a
video cannot be played and a video cannot be imported.** `import_youtube`
refuses rather than falling back to the SSD, because a silent fallback is how
a 7 TB archive ends up on the root partition.
"""
import shutil
from pathlib import Path

from backend import archive_location
from backend.archive_location import ArchiveUnavailable
from backend.storage import IdScopedStorage, is_safe_name

# What we will write to disk, mapped to the mimetype we serve it back as.
# Deliberately closed, for the reason backend/paper/storage.py gives: the file
# is served from our own origin, so an open-ended list is how an .svg ends up
# being a script. `html` is on it because the web importer's output is
# nh3-sanitized before it lands here — and it is still served into a sandboxed
# iframe, because one layer of "this is safe" is not a layer.
STORED_EXTS = {
    'pdf': 'application/pdf',
    'html': 'text/html',
    'mp4': 'video/mp4',
    'webm': 'video/webm',
    'mkv': 'video/x-matroska',
    'm4a': 'audio/mp4',
}

# The kinds whose bytes belong on the archive drive rather than the SSD.
ARCHIVED_KINDS = {'youtube'}

ARCHIVE_COLLECTION = 'study'
ARCHIVE_ENV = 'STUDY_ARCHIVE_ROOT'

_storage = IdScopedStorage('STUDY_ROOT', './data/study')

study_root = _storage.root
delete_source_dir = _storage.delete_dir


def archive_location_state(db=None):
    """Whether the archive drive is there, and why not if it isn't."""
    return archive_location.resolve(ARCHIVE_COLLECTION, ARCHIVE_ENV, db)


def archive_root(db=None, *, create: bool = False) -> Path:
    """The archive root, or `ArchiveUnavailable` explaining why there is none."""
    return archive_location.require_root(
        ARCHIVE_COLLECTION, ARCHIVE_ENV, db, create=create
    )


def source_dir(source_id: str, kind: str = '', *, db=None, create: bool = False):
    """`<root>/<source_id>/`, where the root depends on the kind.

    Raises `ArchiveUnavailable` for an archived kind with no reachable drive —
    never falls back to the SSD, which would be the one failure mode that looks
    like success.
    """
    if not is_safe_name(source_id):
        return None
    if kind in ARCHIVED_KINDS:
        return archive_root(db, create=create) / source_id
    return _storage.root() / source_id


def source_file_path(
    source_id: str, name: str, ext: str, kind: str = '', *, db=None, create: bool = False
) -> Path | None:
    """`<root>/<source_id>/<name>.<ext>`, or None if any part is unsafe or the
    extension is not one we store."""
    ext = ext.lower().lstrip('.')
    if not is_safe_name(name) or ext not in STORED_EXTS:
        return None
    d = source_dir(source_id, kind, db=db, create=create)
    if d is None:
        return None
    return d / f'{name}.{ext}'


def resolve_stored_path(path_str: str, db=None) -> Path | None:
    """Only serve a path that is still a direct grandchild of one of our two
    roots (i.e. `<root>/<source_id>/<file>`), as defence in depth against a
    stored path that has since been tampered with.

    An unreachable archive drive makes an archived path unresolvable rather
    than unsafe — the caller 404s either way, and the viewer says which it was.
    """
    parent = Path(path_str).parent.parent
    if parent == _storage.root():
        return Path(path_str)
    try:
        if parent == archive_root(db):
            return Path(path_str)
    except ArchiveUnavailable:
        return None
    return None


def delete_archived_dir(source_id: str, db=None) -> None:
    """Remove a video's directory from the archive, if the drive is there."""
    if not is_safe_name(source_id):
        return
    try:
        root = archive_root(db)
    except ArchiveUnavailable:
        return
    d = (root / source_id).resolve()
    # Belt and braces, matching IdScopedStorage.delete_dir: only ever delete a
    # direct child of the root.
    if d.parent != root.resolve() or not d.is_dir():
        return
    shutil.rmtree(d, ignore_errors=True)


def mimetype_for(path: str | Path) -> str:
    ext = Path(path).suffix.lower().lstrip('.')
    return STORED_EXTS.get(ext, 'application/octet-stream')
