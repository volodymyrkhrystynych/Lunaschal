import os
import re
import shutil
from pathlib import Path

_SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')

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


def food_root() -> Path:
    return Path(os.environ.get('FOOD_ROOT', './data/food')).expanduser().resolve()


def entry_dir(entry_id: str) -> Path | None:
    # Dot-only names like '..' pass _SAFE_NAME but escape the root.
    if not _SAFE_NAME.match(entry_id) or set(entry_id) == {'.'}:
        return None
    return food_root() / entry_id


def media_path(entry_id: str, media_id: str, ext: str) -> Path | None:
    d = entry_dir(entry_id)
    ext = ext.lower().lstrip('.')
    if d is None or not _SAFE_NAME.match(media_id) or set(media_id) == {'.'}:
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


def resolve_stored_path(path_str: str) -> Path | None:
    """Only serve a path that's still a direct grandchild of the food root
    (i.e. <root>/<entry_id>/<media_id>.<ext>), as defence in depth against a
    stored path that has since been tampered with."""
    path = Path(path_str)
    if path.parent.parent != food_root():
        return None
    return path


def delete_entry_dir(entry_id: str) -> None:
    d = entry_dir(entry_id)
    if d is None:
        return
    # Belt and braces: only ever delete a direct child of the food root.
    d = d.resolve()
    if d.parent != food_root():
        return
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
