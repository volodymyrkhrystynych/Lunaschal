from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

# Extensions we accept for food media, keyed to the two kinds we track.
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}
VIDEO_EXTS = {'mp4', 'mov', 'webm', 'm4v'}

# mime -> canonical extension, for the common types iOS/Safari upload.
_MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/heic': 'heic',
    'image/heif': 'heif',
    'video/mp4': 'mp4',
    'video/quicktime': 'mov',
    'video/webm': 'webm',
    'video/x-m4v': 'm4v',
}

_storage = IdScopedStorage('FOOD_ROOT', './data/food')

food_root = _storage.root
entry_dir = _storage.dir
delete_entry_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def media_path(entry_id: str, media_id: str, ext: str) -> Path | None:
    d = entry_dir(entry_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(media_id):
        return None
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
        return None
    return d / f'{media_id}.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """Pick a stored extension from the upload's mime type, falling back to the
    filename's suffix. Returns None if neither yields an accepted extension."""
    if mime and mime.lower() in _MIME_EXT:
        return _MIME_EXT[mime.lower()]
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            return ext
    return None


def kind_for_ext(ext: str) -> str:
    return 'video' if ext.lower().lstrip('.') in VIDEO_EXTS else 'image'
