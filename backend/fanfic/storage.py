import os
import re
import shutil
from pathlib import Path

_SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')


def fanfic_root() -> Path:
    return Path(os.environ.get('FANFIC_ROOT', './data/fanfic')).expanduser().resolve()


def fic_dir(fic_id: str) -> Path | None:
    if not _SAFE_NAME.match(fic_id):
        return None
    return fanfic_root() / fic_id


def images_dir(fic_id: str) -> Path | None:
    d = fic_dir(fic_id)
    return d / 'images' if d else None


def pdf_path(fic_id: str) -> Path | None:
    d = fic_dir(fic_id)
    return d / 'book.pdf' if d else None


def safe_image_path(fic_id: str, filename: str) -> Path | None:
    if not _SAFE_NAME.match(fic_id) or not _SAFE_NAME.match(filename):
        return None
    if set(fic_id) == {'.'} or set(filename) == {'.'}:
        return None
    base = (fanfic_root() / fic_id / 'images').resolve()
    p = (base / filename).resolve()
    if p.parent != base:
        return None
    return p


def delete_fic_dir(fic_id: str) -> None:
    d = fic_dir(fic_id)
    if d and d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
