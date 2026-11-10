"""Files for spoken to-dos: `<root>/<recording_id>/clip.<ext>`.

Held only until the transcription job has turned the clip into a to-do, then
deleted — a to-do has nowhere to play its audio back, so keeping it would only
grow the data disk. A clip whose transcription failed stays, so the words can
still be recovered from it.

Audio types are the Chat tab's (`backend/chat/storage.py`): the same phone
recorder produces both.
"""
from pathlib import Path

from backend.chat.storage import AUDIO_EXTS, resolve_ext
from backend.storage import IdScopedStorage

_storage = IdScopedStorage('TODOS_ROOT', './data/todos')

todos_root = _storage.root
delete_recording_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def audio_ext(mime: str | None, filename: str | None) -> str | None:
    ext = resolve_ext(mime, filename)
    return ext if ext in AUDIO_EXTS else None


def clip_path(recording_id: str, ext: str) -> Path | None:
    d = _storage.dir(recording_id)
    if d is None or ext not in AUDIO_EXTS:
        return None
    return d / f'clip.{ext}'
