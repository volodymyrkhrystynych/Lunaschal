"""Reader/configuration API for local Kiwix ZIM archives."""
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from bs4 import BeautifulSoup

from backend.db.connection import get_db
from backend.offline_knowledge import archive, kinds, registry

bp = Blueprint('knowledge', __name__, url_prefix='/api/knowledge')

# archiveId -> {'state', 'startedAt', 'finishedAt', 'error'}. Integrity checks
# are in memory only, like the curated-tag scan: the result is a fact about
# this boot's view of the drive, and a check interrupted by a restart should
# read as never having run rather than as a stored verdict.
_checks: dict[str, dict] = {}
_checks_guard = threading.Lock()


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
    # The registry is keyed on absolute paths, so a new root is a different
    # library. Rescan now rather than serving the old root's rows until the TTL
    # happens to lapse.
    try:
        registry.sync(force=True)
    except archive.KnowledgeUnavailable:
        pass
    return jsonify({'path': resolved or '', 'exists': bool(resolved)})


def _with_check(row: dict) -> dict:
    with _checks_guard:
        state = _checks.get(row['id'])
    return {**row, 'check': dict(state)} if state else row


@bp.get('/archives')
def archives():
    try:
        return jsonify([_with_check(row) for row in archive.list_archives()])
    except archive.KnowledgeUnavailable as exc:
        return jsonify({'error': str(exc)}), 503


@bp.post('/archives/rescan')
def rescan():
    try:
        summary = registry.sync(force=True)
    except archive.KnowledgeUnavailable as exc:
        return jsonify({'error': str(exc)}), 503
    archive._open.cache_clear()
    return jsonify({**summary, 'archives': archive.list_archives()})


@bp.patch('/archives/<archive_id>')
def update_archive(archive_id: str):
    body = request.json or {}
    if not registry.row(archive_id):
        return jsonify({'error': 'Archive not found'}), 404
    if 'enabled' in body:
        registry.set_enabled(archive_id, bool(body['enabled']))
    if 'kind' in body:
        try:
            registry.set_kind(archive_id, str(body['kind']))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
    return jsonify(archive._public(registry.row(archive_id)))


@bp.post('/archives/<archive_id>/verify')
def verify_archive(archive_id: str):
    """Run libzim's own integrity check on one archive, in the background.

    Deliberately never part of a scan: `Archive.check()` hashes the entire
    file, which on a 107 GB archive is minutes of disk. It is the answer to
    "this archive behaves oddly", not something a search should ever wait on.
    """
    row = registry.row(archive_id)
    if not row:
        return jsonify({'error': 'Archive not found'}), 404
    with _checks_guard:
        if (_checks.get(archive_id) or {}).get('state') == 'running':
            return jsonify(_checks[archive_id])
        _checks[archive_id] = {
            'state': 'running', 'startedAt': int(time.time()),
            'finishedAt': None, 'error': None,
        }
        state = dict(_checks[archive_id])

    def run():
        result = {'state': 'ok', 'error': None}
        try:
            path = Path(row['path'])
            with archive._lock_for(path):
                zim = archive._archive(path)
                if not archive._call_value(zim, 'check', default=True):
                    result = {'state': 'failed', 'error': 'Integrity check failed'}
        except Exception as exc:
            result = {'state': 'failed', 'error': str(exc)}
        with _checks_guard:
            _checks[archive_id] = {
                **_checks.get(archive_id, {}), **result,
                'finishedAt': int(time.time()),
            }

    threading.Thread(target=run, daemon=True).start()
    return jsonify(state)


@bp.get('/search')
def search():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'q is required'}), 400
    try:
        limit = int(request.args.get('limit') or 20)
    except (ValueError, TypeError):
        return jsonify({'error': 'Bad limit'}), 400

    wanted = [k for k in request.args.getlist('kind') if k in kinds.KINDS]
    archive_ids = [a for a in request.args.getlist('archiveId') if a]
    try:
        found = archive.search_many(
            [query], limit=limit,
            kinds_wanted=wanted or None, archive_ids=archive_ids or None,
        )
    except archive.KnowledgeUnavailable as exc:
        return jsonify({'error': str(exc)}), 503
    return jsonify({
        'results': [{k: v for k, v in hit.items() if not k.startswith('_')}
                    for hit in found['results']],
        'searched': len(found['searched']),
        'skipped': found['skipped'],
        'tookMs': found['tookMs'],
    })


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
