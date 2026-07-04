import os
import shutil
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

bp = Blueprint('files', __name__, url_prefix='/api/files')

FILES_ROOT = Path(os.environ.get('FILES_ROOT', Path.home() / 'notes')).expanduser().resolve()


def _safe(rel: str) -> Path | None:
    p = (FILES_ROOT / rel.lstrip('/')).resolve()
    try:
        p.relative_to(FILES_ROOT)
    except ValueError:
        return None
    return p


@bp.get('')
def list_dir():
    rel = request.args.get('path', '')
    p = _safe(rel) if rel else FILES_ROOT
    FILES_ROOT.mkdir(parents=True, exist_ok=True)
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
            'path': str(item.relative_to(FILES_ROOT)),
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
    data = request.json or {}
    rel = data.get('path', '')
    p = _safe(rel) if rel else None
    if p is None or p == FILES_ROOT:
        return jsonify({'error': 'Invalid path'}), 400
    p.mkdir(parents=True, exist_ok=True)
    return jsonify({'success': True})


@bp.post('/rename')
def rename_file():
    data = request.json or {}
    src = _safe(data.get('from', ''))
    dst = _safe(data.get('to', ''))
    if src is None or dst is None:
        return jsonify({'error': 'Invalid path'}), 400
    if not src.exists():
        return jsonify({'error': 'Source not found'}), 404
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return jsonify({'success': True})


@bp.delete('')
def delete_file():
    rel = request.args.get('path', '')
    p = _safe(rel)
    if p is None:
        return jsonify({'error': 'Invalid path'}), 400
    if not p.exists():
        return jsonify({'error': 'Not found'}), 404
    trash = FILES_ROOT / '.trash'
    trash.mkdir(exist_ok=True)
    dest = trash / p.name
    if dest.exists():
        dest = trash / f'{p.stem}_{int(time.time())}{p.suffix}'
    shutil.move(str(p), str(dest))
    return jsonify({'success': True})
