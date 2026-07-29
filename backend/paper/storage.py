from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

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
