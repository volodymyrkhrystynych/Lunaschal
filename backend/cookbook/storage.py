from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

# Extensions we accept for recipe media, keyed to the three kinds we track.
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}
VIDEO_EXTS = {'mp4', 'mov', 'webm', 'm4v'}
# 'weba' (not 'webm') for audio/webm so an audio upload can never collide with
# VIDEO_EXTS' 'webm' when kind_for_ext has only the extension to go on.
AUDIO_EXTS = {'mp3', 'm4a', 'wav', 'ogg', 'weba', 'aac'}

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
    'audio/mpeg': 'mp3',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/ogg': 'ogg',
    'audio/webm': 'weba',
    'audio/aac': 'aac',
}

_storage = IdScopedStorage('RECIPE_ROOT', './data/recipes')

recipe_root = _storage.root
recipe_dir = _storage.dir
delete_recipe_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def media_path(recipe_id: str, media_id: str, ext: str) -> Path | None:
    d = recipe_dir(recipe_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(media_id):
        return None
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS and ext not in AUDIO_EXTS:
        return None
    return d / f'{media_id}.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """Pick a stored extension from the upload's mime type, falling back to the
    filename's suffix. Returns None if neither yields an accepted extension."""
    if mime and mime.split(';')[0].strip().lower() in _MIME_EXT:
        return _MIME_EXT[mime.split(';')[0].strip().lower()]
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS or ext in AUDIO_EXTS:
            return ext
    return None


def kind_for_ext(ext: str) -> str:
    ext = ext.lower().lstrip('.')
    if ext in VIDEO_EXTS:
        return 'video'
    if ext in AUDIO_EXTS:
        return 'audio'
    return 'image'
