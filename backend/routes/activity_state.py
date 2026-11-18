"""Work / leisure / mix switches from the Watch (through the phone's outbox)
and the phone's Capture tab. See backend/activity_state.py."""
import time

from flask import Blueprint, jsonify, request

from backend import activity_state
from backend.db.connection import get_db

bp = Blueprint('activity_state', __name__, url_prefix='/api/activity-state')

_COLS = 'id, state, at, source'


@bp.post('/switches')
def log_switch():
    """One switch under the id its device minted: a replay from an outbox
    returns the stored row and changes nothing."""
    try:
        row = activity_state.parse_switch(request.get_json(silent=True), int(time.time()))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = get_db()
    db.execute(
        f'INSERT OR IGNORE INTO activity_states({_COLS}, created_at) VALUES (?,?,?,?,?)',
        (row['id'], row['state'], row['at'], row['source'], int(time.time())),
    )
    db.commit()
    stored = db.execute(f'SELECT {_COLS} FROM activity_states WHERE id=?', (row['id'],)).fetchone()
    return jsonify(dict(stored)), 201


@bp.get('/current')
def current():
    """The latest switch by when it was made, not when it arrived: the Watch's
    queue can deliver an older switch after a newer one from the phone."""
    return jsonify(activity_state.current(get_db()))
