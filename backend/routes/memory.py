"""The user-memory document: read it, edit it, undo what the assistant did.

The assistant writes to this without a confirmation card, which is only a
reasonable trade because these routes exist — the Settings editor is where an
immediate write becomes visible and reversible.
"""
from flask import Blueprint, jsonify, request

from backend import memory

bp = Blueprint('memory', __name__, url_prefix='/api/memory')


@bp.get('')
def get_memory():
    return jsonify({'content': memory.get_memory(), 'maxChars': memory.MAX_CHARS})


@bp.put('')
def update_memory():
    body = request.get_json(silent=True) or {}
    content = body.get('content')
    if not isinstance(content, str):
        return jsonify({'error': 'content required'}), 400
    try:
        stored = memory.set_memory(content, source='user')
    except memory.MemoryFull as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'content': stored, 'maxChars': memory.MAX_CHARS})


@bp.get('/revisions')
def list_revisions():
    limit = request.args.get('limit', type=int) or 50
    return jsonify(memory.list_revisions(min(max(limit, 1), 200)))


@bp.post('/revisions/<revision_id>/restore')
def restore_revision(revision_id):
    restored = memory.restore(revision_id)
    if restored is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'content': restored, 'maxChars': memory.MAX_CHARS})
