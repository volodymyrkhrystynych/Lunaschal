"""The user-memory document, and the assistant's own note queue beside it.

Two stores, deliberately not one. The document is the user's: they are its only
writer, and the assistant cannot touch it. The observations are the assistant's,
written instantly by the chat delegate's `remember` tool with no confirmation
card — which is only a reasonable trade because the routes below exist. An
immediate write the user cannot see is one they cannot undo.
"""
from flask import Blueprint, jsonify, request

from backend import memory, observations
import sqlite3
import time
from ulid import ULID
from backend.db.connection import get_db, row_to_dict
from backend.geo import coord_pair
from backend.places import list_places

bp = Blueprint('memory', __name__, url_prefix='/api/memory')


@bp.get('/places')
def places():
    return jsonify([row_to_dict(r) for r in list_places(get_db())])


@bp.put('/places/<place_id>')
@bp.post('/places')
def save_place(place_id=None):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'error': 'Place must be an object'}), 400
    name, notes = body.get('name'), body.get('notes', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        return jsonify({'error': 'Place name must contain 1–120 characters'}), 400
    if not isinstance(notes, str) or len(notes) > 2000:
        return jsonify({'error': 'Place notes must be at most 2000 characters'}), 400
    lat, lon = body.get('latitude'), body.get('longitude')
    pair = coord_pair(lat, lon)
    if (lat is not None or lon is not None) and (isinstance(lat, bool) or isinstance(lon, bool) or not pair or abs(pair[0]) > 90):
        return jsonify({'error': 'Provide both valid latitude and longitude, or leave both empty'}), 400
    radius = body.get('radiusM', 150)
    if isinstance(radius, bool) or not isinstance(radius, int) or not 10 <= radius <= 5000:
        return jsonify({'error': 'Matching radius must be 10–5000 metres'}), 400
    db = get_db()
    if place_id and not db.execute('SELECT 1 FROM saved_places WHERE id=?', (place_id,)).fetchone():
        return jsonify({'error': 'Place not found'}), 404
    now = int(time.time())
    place_id = place_id or str(ULID())
    try:
        db.execute(
            'INSERT INTO saved_places(id,name,notes,latitude,longitude,radius_m,created_at,updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,'
            ' notes=excluded.notes,latitude=excluded.latitude,longitude=excluded.longitude,'
            ' radius_m=excluded.radius_m,updated_at=excluded.updated_at',
            (place_id, name.strip(), notes.strip(), *(pair or (None, None)), radius, now, now),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'error': 'A place with that name already exists'}), 400
    return jsonify(row_to_dict(db.execute('SELECT * FROM saved_places WHERE id=?', (place_id,)).fetchone()))


@bp.delete('/places/<place_id>')
def delete_place(place_id):
    db = get_db()
    db.execute('DELETE FROM saved_places WHERE id=?', (place_id,))
    db.commit()
    return jsonify({'ok': True})


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
