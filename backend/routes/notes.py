"""Notes to self: list what's due for review, dismiss (advances the ladder),
edit (tracked as a revision). Creation has no route — it only ever happens
from backend/delegate/tools.py's create_note_to_self, the same way `remember`
writes to backend/memory.py with no HTTP path of its own.
"""
from flask import Blueprint, jsonify, request

from backend import notes

bp = Blueprint('notes', __name__, url_prefix='/api/notes')


@bp.get('/due')
def get_due():
    return jsonify(notes.list_due())


@bp.post('/<note_id>/dismiss')
def dismiss(note_id):
    updated = notes.dismiss_note(note_id)
    if updated is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(updated)


@bp.put('/<note_id>')
def update(note_id):
    body = request.get_json(silent=True) or {}
    content = body.get('content')
    if not isinstance(content, str) or not content.strip():
        return jsonify({'error': 'content required'}), 400
    updated = notes.edit_note(note_id, content)
    if updated is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(updated)


@bp.get('/<note_id>/revisions')
def revisions(note_id):
    if notes.get_note(note_id) is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(notes.list_revisions(note_id))
