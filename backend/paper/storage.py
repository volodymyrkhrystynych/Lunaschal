from pathlib import Path

from backend.imaging import HEIC_EXTS
from backend.storage import IdScopedStorage, is_safe_name

# What we will actually write to disk, mapped to the mimetype we serve it back
# as. Deliberately closed: the file is written straight to disk from an upload
# and served from our own origin, so an open-ended list is how an .svg ends up
# being a script.
STORED_EXTS = {
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'webp': 'image/webp',
    'gif': 'image/gif',
}

# Accepted at the door on top of those. HEIC/HEIF is what an iPad hands over
# from its photo library — and paper is a tablet feature, so it is the common
# case, not the exotic one — and it is transcoded to JPEG before storage (see
# backend/imaging.py), so it never reaches `pasted_image_path`.
_MIME_EXT = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/heic': 'heic',
    'image/heif': 'heif',
}

_storage = IdScopedStorage('PAPER_ROOT', './data/paper')

paper_root = _storage.root
paper_dir = _storage.dir
delete_paper_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def page_image_path(paper_id: str, page_id: str) -> Path | None:
    d = paper_dir(paper_id)
    if d is None or not is_safe_name(page_id):
        return None
    return d / f'{page_id}.png'


# Pictures pasted onto a page. They sit directly in the paper's directory, not a
# subfolder, because resolve_stored_path only serves a direct grandchild of the
# root; the `img-` prefix is what keeps them from colliding with the
# `<page_id>.png` snapshots alongside them.
def pasted_image_path(paper_id: str, image_id: str, ext: str) -> Path | None:
    d = paper_dir(paper_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(image_id) or not is_safe_name(ext):
        return None
    if ext not in STORED_EXTS:
        return None
    return d / f'img-{image_id}.{ext}'


def resolve_ext(mime: str | None, filename: str | None) -> str | None:
    """Pick an extension for an uploaded picture from its mime type, falling
    back to the filename's suffix. Returns None if neither yields something we
    accept.

    Mime beats extension for the reason `backend/chat/storage.py` says so: a
    phone happily uploads `image.jpg` that is really HEIC bytes. Matching on the
    filename alone is what made every picture pasted onto a page from an iPad
    fail with a 400 — the client sent `IMG_0042.HEIC`, and this list had never
    heard of it.
    """
    if mime and mime.lower() in _MIME_EXT:
        return _MIME_EXT[mime.lower()]
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        if ext == 'jpe':
            ext = 'jpg'
        if ext in STORED_EXTS or ext in HEIC_EXTS:
            return ext
    return None
