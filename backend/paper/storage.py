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


# Pictures pasted onto a page. They sit directly in the paper's directory, not a
# subfolder, because resolve_stored_path only serves a direct grandchild of the
# root; the `img-` prefix is what keeps them from colliding with the
# `<page_id>.png` snapshots alongside them.
def pasted_image_path(paper_id: str, image_id: str, ext: str) -> Path | None:
    d = paper_dir(paper_id)
    if d is None or not is_safe_name(image_id) or not is_safe_name(ext):
        return None
    return d / f'img-{image_id}.{ext}'
