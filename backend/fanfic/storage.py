import os
import re
import shutil
from pathlib import Path

_SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')


def fanfic_root() -> Path:
    return Path(os.environ.get('FANFIC_ROOT', './data/fanfic')).expanduser().resolve()


def fic_dir(fic_id: str) -> Path | None:
    # Dot-only names like '..' pass _SAFE_NAME but escape the root.
    if not _SAFE_NAME.match(fic_id) or set(fic_id) == {'.'}:
        return None
    return fanfic_root() / fic_id


def images_dir(fic_id: str) -> Path | None:
    d = fic_dir(fic_id)
    return d / 'images' if d else None


def pdf_path(fic_id: str) -> Path | None:
    d = fic_dir(fic_id)
    return d / 'book.pdf' if d else None


def safe_image_path(fic_id: str, filename: str) -> Path | None:
    if not _SAFE_NAME.match(filename) or set(filename) == {'.'}:
        return None
    d = images_dir(fic_id)
    if d is None:
        return None
    base = d.resolve()
    p = (base / filename).resolve()
    if p.parent != base:
        return None
    return p


def delete_fic_dir(fic_id: str) -> None:
    d = fic_dir(fic_id)
    if d is None:
        return
    # Belt and braces: only ever delete a direct child of the fanfic root.
    d = d.resolve()
    if d.parent != fanfic_root():
        return
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
