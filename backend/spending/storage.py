"""Receipt photos and clips spoken over a purchase, under ./data/spending/<id>/.

The accepted types and the mime/extension rules are the food log's, imported
rather than copied: both take what a phone camera and its recorder produce, and
two copies of that table are how one learns HEIC and the other doesn't.
"""
from pathlib import Path

from backend.food.storage import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, kind_for_ext, resolve_ext
from backend.storage import IdScopedStorage, is_safe_name

__all__ = ['kind_for_ext', 'resolve_ext', 'media_path', 'entry_dir', 'delete_entry_dir',
           'resolve_stored_path', 'spending_root']

_storage = IdScopedStorage('SPENDING_ROOT', './data/spending')

spending_root = _storage.root
entry_dir = _storage.dir
delete_entry_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def media_path(entry_id: str, media_id: str, ext: str) -> Path | None:
    d = entry_dir(entry_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(media_id):
        return None
    # Video extensions stay acceptable for one reason: a browser's voice clip
    # comes in a webm/mp4 container. The routes decide what may be kept.
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS and ext not in AUDIO_EXTS:
        return None
    return d / f'{media_id}.{ext}'
