"""Files for the photos and voice clips attached to a chat message.

Scoped by conversation rather than by attachment (`<root>/<conversation_id>/
<attachment_id>.<ext>`, the `backend/food/storage.py` layout) because a
conversation is the thing that gets deleted: `DELETE /api/chat/conversations/<id>`
can then drop the whole directory in one call, and the ON DELETE CASCADE on
`chat_attachments.conversation_id` already matches that shape.

Images and audio. Still no video: that would need the ffmpeg audio-extraction
path journal attachments use, and there is nothing in this feature that wants
it. Which is also what makes the ambiguous containers easy here — `webm` and
`mp4` carry either audio or video, and with video refused outright they can only
be the one thing. `backend/food/storage.py` has to be more careful for exactly
the opposite reason.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

# Deliberately the same set backend/food/storage.py accepts, minus video. heic
# and heif are accepted at the door and transcoded to JPEG before storage (see
# backend/imaging.py), so they never actually reach `attachment_path`.
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}

# What a dictated message is stored as. `weba` is what MediaRecorder's
# 'audio/webm;codecs=opus' becomes — the extension exists to keep it apart from
# a video `.webm` on disk, even though nothing here can store one.
AUDIO_EXTS = {'weba', 'm4a', 'mp3', 'wav', 'ogg', 'oga', 'opus', 'aac', 'flac'}

_MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/heic': 'heic',
    'image/heif': 'heif',
    'audio/webm': 'weba',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/flac': 'flac',
}

_storage = IdScopedStorage('CHAT_ROOT', './data/chat')

chat_root = _storage.root
conversation_dir = _storage.dir
delete_conversation_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def attachment_path(conversation_id: str, attachment_id: str, ext: str) -> Path | None:
    d = conversation_dir(conversation_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(attachment_id):
        return None
    if ext not in IMAGE_EXTS and ext not in AUDIO_EXTS:
        return None
    return d / f'{attachment_id}.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """Pick a stored extension from the upload's mime type, falling back to the
    filename's suffix. Returns None if neither yields an accepted extension.

    Mime beats extension for the same reason `backend/journal/storage.py` says
    so: a phone happily uploads `image.jpg` that is really HEIC bytes.
    """
    if mime and mime.lower() in _MIME_EXT:
        return _MIME_EXT[mime.lower()]
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in IMAGE_EXTS or ext in AUDIO_EXTS:
            return ext
    return None


def kind_for_ext(ext: str) -> str:
    """Which `chat_attachments.kind` a stored extension belongs to.

    The recording route does not consult this — it knows it is holding a voice
    memo and says so — but the photo route does, so that an audio file arriving
    through it is filed as what it is rather than as an unreadable picture.
    """
    return 'audio' if ext.lower().lstrip('.') in AUDIO_EXTS else 'image'
