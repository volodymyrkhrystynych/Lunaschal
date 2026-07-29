from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

_storage = IdScopedStorage('FANFIC_ROOT', './data/fanfic')

fanfic_root = _storage.root
fic_dir = _storage.dir
delete_fic_dir = _storage.delete_dir


def images_dir(fic_id: str) -> Path | None:
    d = fic_dir(fic_id)
    return d / 'images' if d else None


def pdf_path(fic_id: str) -> Path | None:
    d = fic_dir(fic_id)
    return d / 'book.pdf' if d else None


def safe_image_path(fic_id: str, filename: str) -> Path | None:
    if not is_safe_name(filename):
        return None
    d = images_dir(fic_id)
    if d is None:
        return None
    base = d.resolve()
    p = (base / filename).resolve()
    if p.parent != base:
        return None
    return p
