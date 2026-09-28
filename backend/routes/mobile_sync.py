from flask import Blueprint, jsonify, request

from backend.mobile_sync.feed import PROTOCOL_VERSION, ResetRequired, page
from backend.mobile_sync.registry import COLLECTIONS
from backend.mobile_sync.operations import apply

bp = Blueprint('mobile_sync', __name__, url_prefix='/api/mobile')


@bp.get('/capabilities')
def capabilities():
    return jsonify({'protocolVersion': PROTOCOL_VERSION, 'collections': list(COLLECTIONS),
                    'captureTimestamp': True, 'maxPageSize': 200,
                    'editableCollections': ['journal_entries']})


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
