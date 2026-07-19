import os
import shutil
import time
from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, request


def make_files_blueprint(
    name: str,
    url_prefix: str,
    root_env: str,
    default_root: str,
    on_rename: Callable[[str, str], None] | None = None,
    on_delete: Callable[[str], None] | None = None,
) -> Blueprint:
    """Build a sandboxed file-CRUD blueprint rooted at `root_env` (or `default_root`).

    The root is re-read from the environment on every call (not cached at import
    time) so it can be overridden per-test via monkeypatch and so multiple mounts
    (Files tab, Notebook) can share this factory with independent roots.
    """
    bp = Blueprint(name, __name__, url_prefix=url_prefix)

    def _root() -> Path:
        return Path(os.environ.get(root_env, default_root)).expanduser().resolve()

    def _safe(rel: str) -> Path | None:
        root = _root()
        p = (root / rel.lstrip('/')).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            return None
        return p

    @bp.get('')
    def list_dir():
        root = _root()
        rel = request.args.get('path', '')
        p = _safe(rel) if rel else root
        root.mkdir(parents=True, exist_ok=True)
        if p is None or not p.exists():
            return jsonify({'error': 'Not found'}), 404
        if not p.is_dir():
            return jsonify({'error': 'Not a directory'}), 400
        entries = []
        for item in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if item.name.startswith('.'):
                continue
            entries.append({
                'name': item.name,
                'path': str(item.relative_to(root)),
                'isDir': item.is_dir(),
                'size': item.stat().st_size if item.is_file() else None,
                'modified': int(item.stat().st_mtime),
            })
        return jsonify(entries)

    @bp.get('/read')
    def read_file():
        rel = request.args.get('path', '')
        p = _safe(rel)
        if p is None:
            return jsonify({'error': 'Invalid path'}), 400
        if not p.is_file():
            return jsonify({'error': 'Not a file'}), 404
        try:
            return jsonify({'content': p.read_text(encoding='utf-8')})
        except UnicodeDecodeError:
            return jsonify({'error': 'Binary file not supported'}), 422

    @bp.post('/write')
    def write_file():
        data = request.json or {}
        p = _safe(data.get('path', ''))
        if p is None:
            return jsonify({'error': 'Invalid path'}), 400
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data.get('content', ''), encoding='utf-8')
        return jsonify({'success': True})

    @bp.post('/mkdir')
    def make_dir():
        root = _root()
        data = request.json or {}
        rel = data.get('path', '')
        p = _safe(rel) if rel else None
        if p is None or p == root:
            return jsonify({'error': 'Invalid path'}), 400
        p.mkdir(parents=True, exist_ok=True)
        return jsonify({'success': True})

    @bp.post('/rename')
    def rename_file():
        data = request.json or {}
        from_rel, to_rel = data.get('from', ''), data.get('to', '')
        src, dst = _safe(from_rel), _safe(to_rel)
        if src is None or dst is None:
            return jsonify({'error': 'Invalid path'}), 400
        if not src.exists():
            return jsonify({'error': 'Source not found'}), 404
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        if on_rename:
            on_rename(from_rel, to_rel)
        return jsonify({'success': True})

    @bp.delete('')
    def delete_file():
        root = _root()
        rel = request.args.get('path', '')
        p = _safe(rel)
        if p is None:
            return jsonify({'error': 'Invalid path'}), 400
        if not p.exists():
            return jsonify({'error': 'Not found'}), 404
        trash = root / '.trash'
        trash.mkdir(exist_ok=True)
        dest = trash / p.name
        if dest.exists():
            dest = trash / f'{p.stem}_{int(time.time())}{p.suffix}'
        shutil.move(str(p), str(dest))
        if on_delete:
            on_delete(rel)
        return jsonify({'success': True})

    return bp


bp = make_files_blueprint(
    'files', '/api/files', 'FILES_ROOT', str(Path.home() / 'notes')
)
