import os
import re
import shutil
from pathlib import Path

_SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')


def paper_root() -> Path:
    return Path(os.environ.get('PAPER_ROOT', './data/paper')).expanduser().resolve()


def paper_dir(paper_id: str) -> Path | None:
    # Dot-only names like '..' pass _SAFE_NAME but escape the root.
    if not _SAFE_NAME.match(paper_id) or set(paper_id) == {'.'}:
        return None
    return paper_root() / paper_id


def page_image_path(paper_id: str, page_id: str) -> Path | None:
    d = paper_dir(paper_id)
    if d is None or not _SAFE_NAME.match(page_id) or set(page_id) == {'.'}:
        return None
    return d / f'{page_id}.png'


def resolve_stored_path(path_str: str) -> Path | None:
    """Only serve a path that's still a direct grandchild of the paper root
    (i.e. <root>/<paper_id>/<page_id>.png), as defence in depth against a
    stored path that has since been tampered with."""
    path = Path(path_str)
    if path.parent.parent != paper_root():
        return None
    return path


def delete_paper_dir(paper_id: str) -> None:
    d = paper_dir(paper_id)
    if d is None:
        return
    # Belt and braces: only ever delete a direct child of the paper root.
    d = d.resolve()
    if d.parent != paper_root():
        return
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
