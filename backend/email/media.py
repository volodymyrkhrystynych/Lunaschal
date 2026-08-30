"""Content-addressed storage for images referenced by email HTML.

Why not the database: a mailbox's images are overwhelmingly logos and
spacers repeated across thousands of messages, and they are bulk binary
data with no queryable structure — exactly what `./data/<feature>/` exists
for everywhere else in this app (fanfic, meetings, journal, food, paper).
This one differs only in *where* that root can point: an email archive is
the first thing here big enough to want a spinning disk rather than the
system SSD, so `EMAIL_MEDIA_ROOT` is expected to be set to a path on it.

Two levels of deduplication, because they save different things:

- **By source** (`email_images.url_hash`, the primary key): stops the same
  campaign logo being *fetched* 4,000 times. This is the one that matters
  for politeness and for wall-clock time. Inline MIME images use a synthetic
  message-id/Content-ID source key instead of a remote URL.
- **By content** (`content_hash`, the filename): stops the same bytes being
  *stored* twice when a sender rotates its CDN hostname or appends a cache
  buster, which URL keying alone cannot see through. This is the one the
  user asked for, and it is what makes a mailbox of identical logos cost
  one file.

Layout: `<root>/<hash[:2]>/<hash[2:4]>/<hash>.<ext>`. The two fan-out levels
are not decoration — the intended target is exFAT, whose directory lookup
degrades badly once a single directory holds tens of thousands of entries,
and a large mailbox produces exactly that.
"""

import hashlib
import os
from pathlib import Path

# Mirrors FANFIC_ROOT / MEETINGS_ROOT / JOURNAL_ROOT / FOOD_ROOT / CHAT_ROOT.
_DEFAULT_ROOT = './data/email/media'
_ENV_VAR = 'EMAIL_MEDIA_ROOT'

# Cheap sanity bound: an inline email image is a logo or a banner, not a
# video. Anything larger is either a mistake or someone using the archive as
# a download target, and the fetcher stops reading at this point.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_EXT_BY_TYPE = {
    'image/jpeg': 'jpg',
    'image/jpg': 'jpg',
    'image/png': 'png',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/avif': 'avif',
    'image/bmp': 'bmp',
    'image/svg+xml': 'svg',
    'image/x-icon': 'ico',
    'image/vnd.microsoft.icon': 'ico',
    'image/tiff': 'tiff',
}


def url_hash(url: str) -> str:
    """Stable key for a URL. Computable at import time, before anything is
    downloaded, which is what lets the sanitizer rewrite an <img> to a local
    path immediately and have the bytes arrive later."""
    return hashlib.sha256(url.strip().encode('utf-8')).hexdigest()


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extension_for(content_type: str | None) -> str:
    base = (content_type or '').split(';')[0].strip().lower()
    return _EXT_BY_TYPE.get(base, 'bin')


def media_root() -> Path:
    return Path(os.environ.get(_ENV_VAR) or _DEFAULT_ROOT)


def is_configured_externally() -> bool:
    return bool(os.environ.get(_ENV_VAR))


def is_available() -> bool:
    """Whether the store can be written to right now.

    An externally configured root must already exist — this deliberately does
    NOT create it. The target is a removable 7 TB disk, and a mountpoint whose
    filesystem is not mounted is just an empty directory on the system SSD:
    creating it and writing on would silently pour a mail archive into the
    117 GB of free space on `/` instead, and look like it was working. Absent
    root means unavailable, which the fetcher treats as "try again later".
    """
    root = media_root()
    if is_configured_externally():
        return root.is_dir() and os.access(root, os.W_OK)
    root.mkdir(parents=True, exist_ok=True)
    return True


def path_for(digest: str, ext: str) -> Path:
    return media_root() / digest[:2] / digest[2:4] / f'{digest}.{ext}'


def store(data: bytes, content_type: str | None) -> tuple[str, str, int]:
    """Write bytes under their content hash. Returns (digest, ext, size).

    Idempotent by construction: an existing file with this name already has
    these exact bytes, so a repeat is a no-op rather than a rewrite. That is
    the whole dedup mechanism — 4,000 copies of one logo converge on one
    path without any lookup table.
    """
    digest = content_hash(data)
    ext = extension_for(content_type)
    dest = path_for(digest, ext)
    if dest.exists():
        return digest, ext, dest.stat().st_size
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp name in the same directory, then rename: a crash or an
    # unplugged disk mid-write must not leave a truncated file sitting at the
    # name that means "these bytes, verified".
    tmp = dest.with_suffix(f'.{ext}.part')
    tmp.write_bytes(data)
    tmp.replace(dest)
    return digest, ext, len(data)


def read(digest: str, ext: str) -> bytes | None:
    p = path_for(digest, ext)
    try:
        return p.read_bytes()
    except OSError:
        return None


def usage() -> dict:
    """Rough store stats for Settings. Walks the tree, so it is not free —
    call it on demand, never per request."""
    root = media_root()
    if not root.is_dir():
        return {'available': False, 'root': str(root), 'fileCount': 0, 'byteSize': 0}
    count = size = 0
    for path in root.rglob('*'):
        if path.is_file() and not path.name.endswith('.part'):
            count += 1
            size += path.stat().st_size
    return {'available': True, 'root': str(root), 'fileCount': count, 'byteSize': size}
