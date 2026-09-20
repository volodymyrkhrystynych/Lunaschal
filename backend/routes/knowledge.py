"""Reader/configuration API for local Kiwix ZIM archives."""
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from bs4 import BeautifulSoup

from backend.db.connection import get_db
from backend.offline_knowledge import archive, catalog, download, kinds, registry

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
    # `writeState` is new with the downloader: the reader only ever needed to
    # know whether the folder was there. Four states rather than a boolean --
    # an unplugged drive, a read-only mount and a permissions problem are three
    # different things to go and fix.
    state = download.root_state()
    return jsonify({
        'path': str(root) if root else '',
        'exists': bool(root and root.is_dir()),
        'writeState': state['state'],
        'writeReason': state['reason'],
    })


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


@bp.get('/catalog')
def catalog_entries():
    """A page of the Kiwix catalogue. Filters are forwarded, not invented --
    `catalog.ALLOWED_FILTERS` is the whitelist, so this proxy cannot be pointed
    at an arbitrary query."""
    filters = {k: v for k, v in request.args.items() if k in catalog.ALLOWED_FILTERS}
    try:
        entries, total = catalog.fetch_entries(**filters)
    except catalog.CatalogUnavailable as exc:
        return jsonify({'error': str(exc)}), 502
    try:
        start = max(int(request.args.get('start') or 0), 0)
    except (TypeError, ValueError):
        start = 0
    return jsonify({'entries': entries, 'total': total, 'start': start})


@bp.get('/catalog/facets')
def catalog_facets():
    try:
        return jsonify(catalog.fetch_facets())
    except catalog.CatalogUnavailable as exc:
        return jsonify({'error': str(exc)}), 502


def _public_download(row: dict) -> dict:
    live = download.progress(row['id']) or {}
    return {
        'id': row['id'],
        'name': row['zim_name'],
        'filename': row['filename'],
        'title': row['title'],
        'status': row['status'],
        'error': row['error'],
        'totalBytes': row['total_bytes'],
        # The live counter wins while a thread is running: the column is only
        # checkpointed every 16 MiB, so the row alone would make a healthy
        # download look stalled between writes.
        'downloadedBytes': live.get('downloadedBytes', row['downloaded_bytes']),
        'bytesPerSecond': live.get('bytesPerSecond'),
        'sourceUrl': row['source_url'],
        'createdAt': row['created_at'],
        'finishedAt': row['finished_at'],
    }


@bp.get('/downloads')
def list_downloads():
    return jsonify([_public_download(row) for row in download.rows()])


@bp.post('/downloads')
def start_download():
    body = request.json or {}
    name = str(body.get('name') or '').strip()
    uuid = str(body.get('uuid') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    try:
        # Re-resolved from the catalogue rather than trusted from the browser:
        # the mirror list and the size decide where bytes come from and how
        # much disk is reserved, and neither should be client input.
        entry = catalog.fetch_entry(name, uuid)
        if not entry or not entry.get('meta4Url'):
            return jsonify({'error': 'That archive is not in the catalogue.'}), 404
        meta = catalog.fetch_meta4(entry['meta4Url'])
    except catalog.CatalogUnavailable as exc:
        return jsonify({'error': str(exc)}), 502
    try:
        return jsonify(_public_download(download.queue(entry, meta))), 201
    except download.DownloadRefused as exc:
        return jsonify({'error': str(exc)}), 409


@bp.post('/downloads/<download_id>/pause')
def pause_download(download_id: str):
    row = download.pause(download_id)
    return (jsonify(_public_download(row)) if row
            else (jsonify({'error': 'Download not found'}), 404))


@bp.post('/downloads/<download_id>/resume')
def resume_download(download_id: str):
    row = download.resume(download_id)
    return (jsonify(_public_download(row)) if row
            else (jsonify({'error': 'Download not found'}), 404))


@bp.delete('/downloads/<download_id>')
def delete_download(download_id: str):
    if not download.delete(download_id):
        return jsonify({'error': 'Download not found'}), 404
    return jsonify({'deleted': True})


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
