import json

from flask import Blueprint, jsonify, request, send_file

from backend.mobile_sync.feed import PROTOCOL_VERSION, ResetRequired, page, status
from backend.mobile_sync.registry import COLLECTIONS
from backend.mobile_sync.operations import apply
from backend.mobile_sync import media
from backend.mobile_sync import drawings
from backend.mobile_sync import fic_download

bp = Blueprint('mobile_sync', __name__, url_prefix='/api/mobile')


@bp.get('/capabilities')
def capabilities():
    return jsonify({'protocolVersion': PROTOCOL_VERSION, 'collections': list(COLLECTIONS),
                    'captureTimestamp': True, 'maxPageSize': 200,
                    'editableCollections': ['journal_entries', 'fic_bookmarks', 'food_entries'],
                    'mediaCollections': list(media.MEDIA), 'nativeDrawingFormat': 'pencilkit-v1',
                    'ficDownload': True, 'syncStatus': True})


@bp.post('/drawings')
def save_drawing():
    request.max_content_length = drawings.MAX_REQUEST
    try:
        body = json.loads(request.form.get('metadata', 'null'))
        result, status = drawings.apply(body, request.files.get('ink'), request.files.get('preview'))
        return jsonify(result), status
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.get('/media')
def media_manifest():
    try:
        return jsonify(media.manifest(request.args.get('collection'),
                                      after=request.args.get('after', ''),
                                      limit=int(request.args.get('limit', '50'))))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, FileExistsError):
        return jsonify(error='Media changed or became unavailable; retry'), 409


@bp.get('/media/<collection>/<record_id>/file')
def media_file(collection, record_id):
    try:
        item, path = media.lookup(collection, record_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError:
        return jsonify(error='Media changed or became unavailable; refresh its manifest'), 409
    if path is None:
        return jsonify(error='Active media file unavailable'), 404
    if request.args.get('sha256') != item['sha256']:
        return jsonify(error='Media version changed; refresh its manifest'), 412
    response = send_file(path, mimetype=item['mime'], conditional=True, etag=item['sha256'])
    response.headers['Cache-Control'] = 'private, no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Archived HTML remains untrusted even when downloaded through this route.
    response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    return response


@bp.get('/fics/<fic_id>/download')
def fic_download_page(fic_id):
    """One fic's chapters ahead of the library download; see fic_download.py."""
    try:
        return jsonify(fic_download.page(fic_id, after=request.args.get('after', ''),
                                         limit=int(request.args.get('limit', str(fic_download.DEFAULT_PAGE_CHAPTERS)))))
    except fic_download.FicNotFound:
        return jsonify(error='Fic not found'), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, FileExistsError):
        return jsonify(error='Media changed or became unavailable; retry'), 409


@bp.post('/operations')
def operation():
    if request.content_length and request.content_length > 2 * 1024 * 1024:
        return jsonify({'error': 'Operation exceeds 2 MB'}), 413
    try:
        result, status = apply(request.get_json(silent=True))
        return jsonify(result), status
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@bp.get('/sync')
def sync():
    try:
        limit = int(request.args.get('limit', '100'))
        collections = request.args.get('collections')
        result = page(token=request.args.get('cursor'),
                      collections=collections.split(',') if collections is not None else None,
                      limit=limit)
        return jsonify(result)
    except ResetRequired as exc:
        return jsonify({'error': str(exc), 'resetRequired': True}), 410
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@bp.post('/sync/status')
def sync_status():
    body = request.get_json(silent=True)
    try:
        return jsonify({'cursors': status(body.get('cursors') if isinstance(body, dict) else None)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
