"""Where downloaded files live, and how the two names for the same file relate.

Unlike every other media feature here, Lunaschal does not *write* these files —
qBittorrent does, from inside a container. So this module is mostly about
translating between the two views of one directory and refusing to serve
anything outside it, rather than about building paths to write to.

Layout is `<root>/<torrent name>/...`, so this follows
backend/newspapers/storage.py's flat shape rather than `IdScopedStorage` — the
top-level directory names come from the torrents themselves, not from our ULIDs,
and `is_safe_name` would reject most of them (spaces, brackets, dots).
"""

import os
import re
from pathlib import Path

# The path the container sees. Fixed by torrent/docker-compose.yml's
# `${TORRENT_DOWNLOAD_DIR}:/downloads` bind mount; the host side is whatever
# TORRENT_ROOT says, and the two are the same directory.
CONTAINER_ROOT = '/downloads'


def torrent_root() -> Path:
    """Host-side download directory. Re-read from the env var on every call (not
    cached) so tests can monkeypatch it per-case, same as the other roots."""
    return Path(os.environ.get('TORRENT_ROOT', './data/torrents')).expanduser().resolve()


def to_host_path(container_path: str) -> Path | None:
    """`/downloads/Foo/bar.mkv` -> `<root>/Foo/bar.mkv`.

    qBittorrent reports paths in its own namespace; nothing on the host can open
    those. Returns None for a path outside the bind mount — which should be
    impossible, and is exactly why it's worth noticing rather than joining
    blindly onto the root.
    """
    if not container_path:
        return None
    normalized = os.path.normpath(container_path)
    if normalized != CONTAINER_ROOT and not normalized.startswith(CONTAINER_ROOT + os.sep):
        return None
    relative = normalized[len(CONTAINER_ROOT) :].lstrip(os.sep)
    return torrent_root() / relative if relative else torrent_root()


def to_container_path(host_path: str | Path) -> str | None:
    """The inverse, for handing a save path to the client."""
    root = torrent_root()
    try:
        relative = Path(host_path).expanduser().resolve().relative_to(root)
    except ValueError:
        return None
    return CONTAINER_ROOT if str(relative) == '.' else f'{CONTAINER_ROOT}/{relative.as_posix()}'


def resolve_download_path(container_path: str) -> Path | None:
    """Translate and then *prove* the result is inside the download root.

    The file-serving route hands whatever qBittorrent reported straight to
    `send_file`, so this is the boundary. `resolve()` is what does the real work:
    it collapses any `..` and follows symlinks, so a torrent that shipped a
    symlink pointing at /etc or at the Lunaschal database resolves to a path
    outside the root and is refused. Checking the string before resolving would
    not catch that.

    Existence is deliberately not checked here — a partially-downloaded file is
    a legitimate 404 from the caller, not a traversal attempt.
    """
    host = to_host_path(container_path)
    if host is None:
        return None
    root = torrent_root()
    resolved = host.resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


# exFAT rejects these outright, and torrent names are full of colons ("S01:E02",
# "Artist: Album") and question marks. Written into a filename they fail with a
# bare EINVAL from deep inside the client, which reads as a corrupt download
# rather than as a filesystem limitation.
_EXFAT_FORBIDDEN = re.compile(r'["*/:<>?\\|]')
_CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]')


def sanitize_for_exfat(name: str) -> str:
    """Make a torrent name safe to use as a directory on the exFAT data drive.

    Only used when we choose a save path ourselves. Collapses each forbidden
    character to an underscore rather than dropping it, so two names that differ
    only in punctuation don't collide into one directory.
    """
    cleaned = _CONTROL_CHARS.sub('', name)
    cleaned = _EXFAT_FORBIDDEN.sub('_', cleaned)
    # exFAT also silently mangles trailing dots and spaces.
    cleaned = cleaned.rstrip(' .')
    return cleaned or 'untitled'
