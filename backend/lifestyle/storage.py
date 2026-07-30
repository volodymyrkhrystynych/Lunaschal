"""On-disk storage for daily selfies.

Images live beside the DB under ./data/lifestyle/<selfie_id>/, never as SQLite
blobs (docs/lifestyle-tab.md §5), using the same IdScopedStorage layout as the
fanfic/meetings/food media roots.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'}

# mime -> canonical extension. `getUserMedia` captures are canvas-encoded to
# image/jpeg or image/png; the rest cover a phone's native camera roll upload.
_MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/heic': 'heic',
    'image/heif': 'heif',
}

_storage = IdScopedStorage('LIFESTYLE_ROOT', './data/lifestyle')

lifestyle_root = _storage.root
selfie_dir = _storage.dir
delete_selfie_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def selfie_path(selfie_id: str, ext: str) -> Path | None:
    d = selfie_dir(selfie_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(selfie_id) or ext not in IMAGE_EXTS:
        return None
    return d / f'selfie.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """Pick a stored extension from the upload's mime type, falling back to the
    filename's suffix. Returns None if neither yields an accepted image type."""
    if mime and mime.lower() in _MIME_EXT:
        return _MIME_EXT[mime.lower()]
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in IMAGE_EXTS:
            return ext
    return None
