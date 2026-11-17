"""Clips spoken into an event's description, under `<CALENDAR_ROOT>/<event_id>/`.

Audio only, so unlike the food log's storage nothing here has to tell a voice
memo from a video: a `webm` or `mp4` upload is a recording by definition.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

_MIME_EXT = {
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/flac': 'flac',
    'audio/webm': 'webm',
}
_EXTS = {*_MIME_EXT.values(), 'mp4', 'oga', 'weba'}

_storage = IdScopedStorage('CALENDAR_ROOT', './data/calendar')

calendar_root = _storage.root
delete_event_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def recording_path(event_id: str, recording_id: str, ext: str) -> Path | None:
    d = _storage.dir(event_id)
    if d is None or not is_safe_name(recording_id) or ext not in _EXTS:
        return None
    return d / f'{recording_id}.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """The stored extension from the upload's mime type (codec parameters
    ignored), falling back to the filename. None if neither is audio."""
    base = (mime or '').split(';')[0].strip().lower()
    if base in _MIME_EXT:
        return _MIME_EXT[base]
    suffix = Path(filename or '').suffix.lower().lstrip('.')
    return suffix if suffix in _EXTS else None
