"""The user-memory document, and the assistant's own note queue beside it.

Two stores, deliberately not one. The document is the user's: they are its only
writer, and the assistant cannot touch it. The observations are the assistant's,
written instantly by the chat delegate's `remember` tool with no confirmation
card — which is only a reasonable trade because the routes below exist. An
immediate write the user cannot see is one they cannot undo.
"""
from flask import Blueprint, jsonify, request

from backend import memory, observations

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


@bp.get('/observations')
def list_observations():
    """What the assistant has noted about the user and not yet filed.

    Returns the whole pending queue, not the slice that reaches the system
    prompt: this is the page where they go to disagree with one.
    """
    return jsonify({
        'observations': observations.pending(),
        'maxPending': observations.MAX_PENDING,
    })


@bp.delete('/observations/<observation_id>')
def delete_observation(observation_id):
    if not observations.delete_observation(observation_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})
