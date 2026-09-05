"""File storage for study sources: <STUDY_ROOT>/<source_id>/<file>.

Same `<root>/<id>/…` layout (and the same `IdScopedStorage`) as fanfic,
meetings, food and paper.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

# What we will write to disk, mapped to the mimetype we serve it back as.
# Deliberately closed, for the reason backend/paper/storage.py gives: the file
# is served from our own origin, so an open-ended list is how an .svg ends up
# being a script. `html` is on it because the web importer's output is
# nh3-sanitized before it lands here — and it is still served into a sandboxed
# iframe, because one layer of "this is safe" is not a layer.
STORED_EXTS = {
    'pdf': 'application/pdf',
    'html': 'text/html',
    'mp4': 'video/mp4',
    'webm': 'video/webm',
    'mkv': 'video/x-matroska',
    'm4a': 'audio/mp4',
}

_storage = IdScopedStorage('STUDY_ROOT', './data/study')

study_root = _storage.root
source_dir = _storage.dir
delete_source_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def source_file_path(source_id: str, name: str, ext: str) -> Path | None:
    """`<root>/<source_id>/<name>.<ext>`, or None if any part is unsafe or the
    extension is not one we store."""
    d = source_dir(source_id)
    ext = ext.lower().lstrip('.')
    if d is None or not is_safe_name(name) or ext not in STORED_EXTS:
        return None
    return d / f'{name}.{ext}'


def mimetype_for(path: str | Path) -> str:
    ext = Path(path).suffix.lower().lstrip('.')
    return STORED_EXTS.get(ext, 'application/octet-stream')
