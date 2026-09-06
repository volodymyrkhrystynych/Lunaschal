"""Reader/configuration API for local Kiwix ZIM archives."""
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from bs4 import BeautifulSoup

from backend.db.connection import get_db
from backend.offline_knowledge import archive

bp = Blueprint('knowledge', __name__, url_prefix='/api/knowledge')


@bp.get('/config')
def get_config():
    root = archive.configured_root()
    return jsonify({'path': str(root) if root else '', 'exists': bool(root and root.is_dir())})


@bp.put('/config')
def set_config():
    raw = (request.json or {}).get('path')
    if raw is None or not str(raw).strip():
        resolved = None
    else:
        try:
            target = Path(str(raw)).expanduser().resolve()
        except (OSError, RuntimeError):
            return jsonify({'error': 'Bad path'}), 400
        if not target.is_dir():
            return jsonify({'error': 'Not a directory'}), 400
        resolved = str(target)
    db = get_db()
    db.execute(
        'UPDATE settings SET knowledge_root=?, updated_at=? WHERE id=1',
        (resolved, int(time.time())),
    )
    db.commit()
    archive._open.cache_clear()
    return jsonify({'path': resolved or '', 'exists': bool(resolved)})


@bp.get('/archives')
def archives():
    try:
        return jsonify(archive.list_archives())
    except archive.KnowledgeUnavailable as exc:
        return jsonify({'error': str(exc)}), 503


@bp.get('/search')
def search():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'q is required'}), 400
    try:
        limit = int(request.args.get('limit') or 20)
        return jsonify(archive.search(query, limit=limit))
    except (ValueError, TypeError):
        return jsonify({'error': 'Bad limit'}), 400
    except archive.KnowledgeUnavailable as exc:
        return jsonify({'error': str(exc)}), 503


@bp.get('/archives/<archive_id>/content/<path:entry_path>')
def content(archive_id: str, entry_path: str):
    try:
        body, mime, _ = archive.read_entry(archive_id, entry_path)
    except archive.ArchiveNotFound:
        return jsonify({'error': 'Article not found'}), 404
    except archive.KnowledgeUnavailable as exc:
        return jsonify({'error': str(exc)}), 503
    if mime in ('text/html', 'application/xhtml+xml'):
        # Root-relative URLs inside a ZIM refer to that archive, not Flask's
        # application root. Rebase the common resource/link attributes while
        # leaving relative URLs alone—the response URL already resolves those
        # against the article's own directory.
        soup = BeautifulSoup(body.decode('utf-8', 'replace'), 'html.parser')
        prefix = f'/api/knowledge/archives/{archive_id}/content/'
        for tag in soup.find_all(True):
            for attr in ('href', 'src', 'poster'):
                value = tag.get(attr)
                if (
                    isinstance(value, str)
                    and value.startswith('/')
                    and not value.startswith('//')
                ):
                    tag[attr] = prefix + value.lstrip('/')
        body = str(soup).encode('utf-8')
    response = Response(body, content_type=mime)
    # Archived pages are untrusted documents. They may display their own static
    # assets, but cannot run script, submit forms, or contact the internet.
    response.headers['Content-Security-Policy'] = (
        "default-src 'self' data: blob:; script-src 'none'; connect-src 'none'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "media-src 'self' data: blob:; object-src 'none'; form-action 'none'; "
        "frame-ancestors 'self'"
    )
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
