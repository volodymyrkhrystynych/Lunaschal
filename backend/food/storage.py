from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

# Extensions we accept for food media, keyed to the three kinds we track.
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}
VIDEO_EXTS = {'mp4', 'mov', 'webm', 'm4v'}
# A meal can be spoken as well as photographed. `webm` and `mp4` are missing
# here on purpose: both containers carry either, so extension alone cannot tell
# a voice memo from a clip of the kitchen — `resolve_ext` settles those from the
# upload's mime type, and an ambiguous one stays a video (audio in a <video>
# costs a blank frame; the reverse throws the picture away).
AUDIO_EXTS = {'m4a', 'mp3', 'wav', 'ogg', 'oga', 'opus', 'aac', 'flac', 'weba'}

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
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/flac': 'flac',
    'audio/webm': 'weba',
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
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS and ext not in AUDIO_EXTS:
        return None
    return d / f'{media_id}.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """Pick a stored extension from the upload's mime type, falling back to the
    filename's suffix. Returns None if neither yields an accepted extension."""
    if mime and mime.lower() in _MIME_EXT:
        return _MIME_EXT[mime.lower()]
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS or ext in AUDIO_EXTS:
            return ext
    return None


def kind_for_ext(ext: str) -> str:
    ext = ext.lower().lstrip('.')
    if ext in AUDIO_EXTS:
        return 'audio'
    return 'video' if ext in VIDEO_EXTS else 'image'
