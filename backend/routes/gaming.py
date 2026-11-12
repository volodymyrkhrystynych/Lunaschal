"""The gaming lock's REST surface: its state, its settings, and the override.

The normal way to unlock is the chat — `end_workday` stages an `unlock_gaming`
card and accepting it lifts the lock (backend/routes/chat.py). The override
here is the way round the review, and costs a reason and a missed item in the
day's review. See backend/gaming/lock.py.
"""
from flask import Blueprint, jsonify, request

from backend.db.connection import get_db
from backend.gaming import lock

bp = Blueprint('gaming', __name__, url_prefix='/api/gaming')


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify({'error': str(error)}), 400


@bp.get('')
def get_status():
    return jsonify(lock.status(get_db()))


@bp.patch('/settings')
def patch_settings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError('expected a JSON object')
    db = get_db()
    lock.update_settings(db, body)
    return jsonify(lock.status(db))


@bp.post('/override')
def override():
    body = request.get_json(silent=True) or {}
    reason = body.get('reason')
    if not isinstance(reason, str):
        raise ValueError('reason required')
    db = get_db()
    state = lock.lift(db, 'override', reason=reason)
    if body.get('pauseInference') is True:
        from backend.routes.settings import pause_inference_now
        pause_inference_now()
    return jsonify(state)
