"""Where a spoken review answer waits to be heard.

`<root>/<attempt_id>/answer.<ext>`, one clip per attempt, and only until the
grader has transcribed it: the words become the attempt's `answer`, which is
what the review keeps, so the audio is scratch. It lives on disk rather than in
the request because the phone moves on to the next card the moment recording
stops — nobody is waiting on the transcription, so it runs on the job queue.
"""
from pathlib import Path

from backend.chat.storage import AUDIO_EXTS, resolve_ext as _resolve_any_ext
from backend.storage import IdScopedStorage

_storage = IdScopedStorage('LEARNING_ROOT', './data/learning')

attempt_dir = _storage.dir
delete_attempt_dir = _storage.delete_dir


def resolve_audio_ext(mime: str | None, filename: str | None) -> str | None:
    """The stored extension for an uploaded clip, or None if it isn't audio."""
    ext = _resolve_any_ext(mime, filename)
    return ext if ext in AUDIO_EXTS else None


def clip_path(attempt_id: str, ext: str) -> Path | None:
    d = attempt_dir(attempt_id)
    if d is None or ext not in AUDIO_EXTS:
        return None
    return d / f'answer.{ext}'


def find_clip(attempt_id: str) -> Path | None:
    """The attempt's clip still waiting to be transcribed, if there is one."""
    d = attempt_dir(attempt_id)
    if d is None or not d.is_dir():
        return None
    return next((p for p in sorted(d.glob('answer.*')) if p.is_file()), None)
