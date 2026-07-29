import os
import re
import shutil
from pathlib import Path

_SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')


def is_safe_name(name: str) -> bool:
    # Dot-only names like '..' pass _SAFE_NAME but escape the root.
    return bool(_SAFE_NAME.match(name)) and set(name) != {'.'}


class IdScopedStorage:
    """Filesystem helper for a `<root>/<id>/...` storage layout, shared by the
    fanfic, meetings, food and paper features. The root is re-read from the
    env var on every call (not cached), so tests can monkeypatch it per-case."""

    def __init__(self, root_env_var: str, default_root: str):
        self._root_env_var = root_env_var
        self._default_root = default_root

    def root(self) -> Path:
        return Path(os.environ.get(self._root_env_var, self._default_root)).expanduser().resolve()

    def dir(self, item_id: str) -> Path | None:
        if not is_safe_name(item_id):
            return None
        return self.root() / item_id

    def resolve_stored_path(self, path_str: str) -> Path | None:
        """Only serve a path that's still a direct grandchild of the root
        (i.e. <root>/<item_id>/<file>), as defence in depth against a stored
        path that has since been tampered with."""
        path = Path(path_str)
        if path.parent.parent != self.root():
            return None
        return path

    def delete_dir(self, item_id: str) -> None:
        d = self.dir(item_id)
        if d is None:
            return
        # Belt and braces: only ever delete a direct child of the root.
        d = d.resolve()
        if d.parent != self.root():
            return
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
