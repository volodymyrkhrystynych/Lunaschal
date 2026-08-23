import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, request, send_file

# Ceilings for the recursive /tree walk below.
MAX_TREE_DEPTH = 12
MAX_TREE_ENTRIES = 5000


def make_files_blueprint(
    name: str,
    url_prefix: str,
    root_env: str,
    default_root: str,
    on_rename: Callable[[str, str], None] | None = None,
    on_delete: Callable[[str], None] | None = None,
    root_resolver: Callable[[], Path] | None = None,
    extra_routes: Callable[[Blueprint, Callable[[str], Path | None], Callable[[], Path]], None]
    | None = None,
) -> Blueprint:
    """Build a sandboxed file-CRUD blueprint rooted at `root_env` (or `default_root`).

    The root is re-read from the environment on every call (not cached at import
    time) so it can be overridden per-test via monkeypatch and so multiple mounts
    (Files tab, Notebook) can share this factory with independent roots.

    `root_resolver`, when given, replaces the env-var lookup entirely — used by
    the `files` mount so Settings → Files can point it at a DB-configured root;
    Notebook passes neither param and keeps the env-var-only behavior above.

    `extra_routes`, when given, is called with `(bp, _safe, _root)` before the
    blueprint is returned, so routes that need the same traversal guard (the
    Files mount's upload/content/config endpoints) can be registered without
    exposing `_safe`/`_root` outside this factory — Notebook doesn't pass this
    either, so it gets none of those routes.
    """
    bp = Blueprint(name, __name__, url_prefix=url_prefix)

    def _root() -> Path:
        if root_resolver:
            return root_resolver()
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

    # A whole-tree walk, which the single-level list_dir above can't answer
    # without one request per directory. Notebook's index page regenerates a
    # link tree of every note from this on open, so it is a hot path on a
    # directory the user keeps adding to — hence the two ceilings below rather
    # than an unbounded os.walk. They are generous enough that a real notebook
    # never meets them and low enough that a stray symlink loop or an
    # accidentally-nested checkout can't hang the request.
    @bp.get('/tree')
    def list_tree():
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        truncated = False

        def walk(directory: Path, depth: int) -> None:
            nonlocal truncated
            if depth > MAX_TREE_DEPTH or truncated:
                return
            try:
                items = sorted(
                    directory.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
                )
            except OSError:
                return
            for item in items:
                # Dotfiles are hidden here for the same reason list_dir hides
                # them, and it is what keeps the .trash/ that delete_file writes
                # out of the tree — a deleted note must not come back as a link.
                if item.name.startswith('.'):
                    continue
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    return
                is_dir = item.is_dir()
                entries.append({
                    'name': item.name,
                    'path': str(item.relative_to(root)),
                    'isDir': is_dir,
                    'size': None if is_dir else item.stat().st_size,
                    'modified': int(item.stat().st_mtime),
                })
                # is_dir() follows symlinks; not recursing into a linked
                # directory is what makes the depth cap a backstop rather than
                # the only thing standing between us and a symlink cycle.
                if is_dir and not item.is_symlink():
                    walk(item, depth + 1)

        walk(root, 0)
        return jsonify({'entries': entries, 'truncated': truncated})

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

    if extra_routes:
        extra_routes(bp, _safe, _root)

    return bp


def _files_extra_routes(
    bp: Blueprint, _safe: Callable[[str], Path | None], _root: Callable[[], Path]
) -> None:
    """Upload/content/config routes for the `files` mount only.

    Kept out of the shared factory body (registered instead via
    `extra_routes=`) so Notebook, which shares `make_files_blueprint` for its
    own independent root, never gets a settings-backed config endpoint or the
    binary upload/download surface — Notebook only ever holds plain-text notes.
    """

    def _unique_dest(dest: Path) -> Path:
        if not dest.exists():
            return dest
        stem, suffix, i = dest.stem, dest.suffix, 1
        while True:
            candidate = dest.with_name(f'{stem}_{i}{suffix}')
            if not candidate.exists():
                return candidate
            i += 1

    @bp.post('/upload')
    def upload_files():
        dir_rel = request.form.get('path', '')
        dest_dir = _safe(dir_rel) if dir_rel else _root()
        if dest_dir is None:
            return jsonify({'error': 'Invalid path'}), 400
        dest_dir.mkdir(parents=True, exist_ok=True)

        uploaded: list[dict] = []
        errors: list[dict] = []
        root = _root()
        for storage in request.files.getlist('file'):
            name = os.path.basename((storage.filename or '').strip())
            if not name or name in ('.', '..'):
                errors.append({'name': storage.filename or '', 'error': 'Invalid filename'})
                continue
            dest = _unique_dest(dest_dir / name)
            try:
                dest.relative_to(root)
            except ValueError:
                errors.append({'name': name, 'error': 'Invalid path'})
                continue
            storage.save(str(dest))
            uploaded.append({
                'name': dest.name,
                'path': str(dest.relative_to(root)),
                'size': dest.stat().st_size,
            })
        return jsonify({'uploaded': uploaded, 'errors': errors})

    @bp.get('/content')
    def file_content():
        rel = request.args.get('path', '')
        p = _safe(rel)
        if p is None:
            return jsonify({'error': 'Invalid path'}), 400
        if not p.is_file():
            return jsonify({'error': 'Not found'}), 404
        download = request.args.get('download', '0') == '1'
        mimetype = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        return send_file(
            p, mimetype=mimetype, as_attachment=download, download_name=p.name
        )

    @bp.get('/config')
    def get_files_config():
        from backend.db.connection import get_db
        from backend.files_config import get_config

        return jsonify(get_config(get_db()))

    @bp.put('/config')
    def put_files_config():
        from backend.db.connection import get_db
        from backend.files_config import set_config, validate_root

        body = request.get_json(silent=True) or {}
        path = body.get('destination')
        if path is not None:
            problem = validate_root(str(path))
            if problem:
                return jsonify({'error': problem}), 400
        try:
            cfg = set_config(get_db(), path=None if path is None else str(path))
        except ValueError:
            return jsonify({'error': 'Settings row missing'}), 500
        return jsonify(cfg)


def _files_root() -> Path:
    """`files_root` from Settings, falling back to `FILES_ROOT` and then the
    historical default — same precedence backup_config.get_config uses for
    `backup_path` vs. its env-file fallback."""
    from backend.db.connection import get_db
    from backend.files_config import get_config

    path = get_config(get_db())['path']
    if not path:
        path = os.environ.get('FILES_ROOT', str(Path.home() / 'notes'))
    return Path(path).expanduser().resolve()


bp = make_files_blueprint(
    'files',
    '/api/files',
    'FILES_ROOT',
    str(Path.home() / 'notes'),
    root_resolver=_files_root,
    extra_routes=_files_extra_routes,
)
