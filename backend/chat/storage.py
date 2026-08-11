"""Files for photos attached to a chat message.

Scoped by conversation rather than by attachment (`<root>/<conversation_id>/
<attachment_id>.<ext>`, the `backend/food/storage.py` layout) because a
conversation is the thing that gets deleted: `DELETE /api/chat/conversations/<id>`
can then drop the whole directory in one call, and the ON DELETE CASCADE on
`chat_attachments.conversation_id` already matches that shape.

Images only. Video in chat would need the ffmpeg audio-extraction path journal
attachments use, and there is nothing in this feature that wants it.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

# Deliberately the same set backend/food/storage.py accepts, minus video. heic
# and heif are accepted at the door and transcoded to JPEG before storage (see
# backend/imaging.py), so they never actually reach `attachment_path`.
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}

_MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/heic': 'heic',
    'image/heif': 'heif',
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
    if ext not in IMAGE_EXTS:
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
        if ext in IMAGE_EXTS:
            return ext
    return None
