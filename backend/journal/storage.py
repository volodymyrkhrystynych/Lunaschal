"""On-disk storage for journal entry attachments (audio clips, video and photos).

Files live beside the DB under ./data/journal/<attachment_id>/, using the same
IdScopedStorage layout as the fanfic/meetings/lifestyle/food media roots — one
directory per attachment, so deleting one is an rmtree of a directory whose name
we control rather than an unlink of a path read back out of the database.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'gif'}

# Whatever a phone voice-memo app or a desktop recorder produces. ffmpeg decodes
# all of them on the way into the STT backend (backend/routes/stt.py), so this
# list is about what we are willing to store, not what Whisper/Parakeet can read.
AUDIO_EXTS = {'m4a', 'mp3', 'wav', 'ogg', 'oga', 'opus', 'webm', 'flac', 'aac', 'mp4'}

VIDEO_EXTS = {'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', '3gp'}

# mp4/webm are containers that carry either, so an extension alone cannot always
# tell audio from video — the upload's mime type is the authority and this set is
# only about which extensions need it. See _kind_from_ext for the tie-break.
_AMBIGUOUS_EXTS = AUDIO_EXTS & VIDEO_EXTS

_MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/heic': 'heic',
    'image/heif': 'heif',
    'image/gif': 'gif',
    'audio/mpeg': 'mp3',
    'audio/mp3': 'mp3',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/m4a': 'm4a',
    'audio/aac': 'aac',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/wave': 'wav',
    'audio/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/webm': 'webm',
    'audio/flac': 'flac',
    'audio/x-flac': 'flac',
    # iOS hands over .mov from the camera roll and .mp4 from most everything
    # else; both arrive with a video/* type even when the clip is a talking head
    # whose only interesting content is the speech.
    'video/mp4': 'mp4',
    'video/quicktime': 'mov',
    'video/x-m4v': 'm4v',
    'video/webm': 'webm',
    'video/x-matroska': 'mkv',
    'video/x-msvideo': 'avi',
    'video/3gpp': '3gp',
}

_storage = IdScopedStorage('JOURNAL_ROOT', './data/journal')

journal_root = _storage.root
attachment_dir = _storage.dir
delete_attachment_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def _kind_from_ext(ext: str) -> str | None:
    """'audio' | 'video' | 'image' for an accepted extension, else None.

    An ambiguous container extension resolves to video. Getting it wrong either
    way is survivable, but audio shown in a <video> element still plays (just
    with a blank frame), whereas video shown in an <audio> element silently
    throws the picture away — so the reversible mistake is the default.
    """
    ext = ext.lower().lstrip('.')
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in _AMBIGUOUS_EXTS or ext in VIDEO_EXTS:
        return 'video' if ext in VIDEO_EXTS else 'audio'
    if ext in AUDIO_EXTS:
        return 'audio'
    return None


def kind_for_ext(ext: str) -> str | None:
    return _kind_from_ext(ext)


def resolve_upload(mime: str | None, filename: str | None) -> tuple[str, str] | None:
    """Map an upload to the (extension, kind) it will be stored as, or None if
    it is not a media type we accept.

    The mime type wins over the filename: a browser file input and a paste both
    report it reliably, and for the mp4/webm containers it is the only thing
    that distinguishes a voice memo from a video. Filenames off a phone are
    sometimes extensionless, which is why the fallback exists at all.
    """
    if mime:
        base = mime.split(';', 1)[0].strip().lower()
        ext = _MIME_EXT.get(base)
        if ext:
            top = base.split('/', 1)[0]
            kind = {'audio': 'audio', 'video': 'video', 'image': 'image'}.get(top)
            if kind:
                return ext, kind
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        kind = _kind_from_ext(ext)
        if kind:
            return ext, kind
    return None


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    resolved = resolve_upload(mime, filename)
    return resolved[0] if resolved else None


def attachment_path(attachment_id: str, ext: str) -> Path | None:
    d = attachment_dir(attachment_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(attachment_id) or not _kind_from_ext(ext):
        return None
    return d / f'attachment.{ext}'
