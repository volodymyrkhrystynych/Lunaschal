"""The coach's REST surface: day reviews, and explaining a day from a client.

Excuses normally arrive through chat (`record_excuse` in backend/coach/tools.py);
this route is the same call for the review card's own "Explain" button.
"""
from datetime import date

from flask import Blueprint, jsonify, request

from backend.coach import review
from backend.day_boundary import day_key_for

bp = Blueprint('coach', __name__, url_prefix='/api/coach')


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify({'error': str(error)}), 400


def _day(day: str) -> str:
    try:
        if date.fromisoformat(day).isoformat() == day:
            return day
    except ValueError:
        pass
    raise ValueError('day must be YYYY-MM-DD')


@bp.get('/reviews')
def reviews():
    """The last `days` reviewed days, newest first, plus today's draft."""
    try:
        days = max(1, min(int(request.args.get('days', 7)), 60))
    except ValueError:
        raise ValueError('days must be a number')
    return jsonify({'today': review.draft(day_key_for()), 'days': review.recent(days)})


@bp.get('/reviews/<day>')
def one_review(day):
    day = _day(day)
    return jsonify(review.get(day) or review.draft(day))


@bp.post('/reviews/<day>/excuses')
def add_excuse(day):
    body = request.get_json(silent=True) or {}
    excuse, updated = review.record_excuse(
        _day(day), body.get('targetKind', ''), body.get('target'), body.get('reason', ''),
        body.get('kind', 'external'), body.get('obstacle'))
    return jsonify({'excuse': excuse, 'review': updated}), 201


@bp.post('/reply')
def reply():
    """A text reply typed on a notification, with the app closed: it becomes a
    message in today's chat and the agent answers it in the background — so
    "busy day, couldn't" from the lock screen is recorded by `record_excuse`
    without the app ever opening. The phone sends its own id for the
    message, so a retried reply is one message."""
    import time

    from backend.briefing_job import find_or_create_day_conversation
    from backend.chat import autoreply
    from backend.db.connection import get_db

    body = request.get_json(silent=True) or {}
    text = body.get('text')
    message_id = body.get('id')
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('text must be 1–4000 characters')
    if not isinstance(message_id, str) or not message_id.isalnum() or len(message_id) > 64:
        raise ValueError('id must be a ULID')
    now = int(time.time())
    db = get_db()
    conv_id = find_or_create_day_conversation(db, day_key_for(now), now)
    inserted = db.execute('INSERT OR IGNORE INTO messages (id, conversation_id, role, content, created_at) '
                          "VALUES (?,?,'user',?,?)", (message_id, conv_id, text.strip(), now)).rowcount
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conv_id))
    db.commit()
    reply_id = autoreply.start_reply(conv_id, message_id) if inserted else None
    return jsonify({'conversationId': conv_id, 'messageId': message_id, 'replyId': reply_id}), 201


@bp.get('/settings')
def get_settings():
    from backend.coach import config
    from backend.db.connection import get_db
    return jsonify(config.read_all(get_db()))


@bp.patch('/settings')
def patch_settings():
    from backend.coach import config
    from backend.db.connection import get_db
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError('A settings object is required')
    return jsonify(config.update(get_db(), body))


@bp.get('/stats')
def stats():
    from backend.coach import engine
    return jsonify(engine.stats())


@bp.post('/nudges/<decision_id>/response')
def nudge_response(decision_id):
    from backend.capture_time import optional_capture_time
    from backend.coach import engine
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(engine.respond(decision_id, body.get('action', ''), optional_capture_time(request.args)))
    except LookupError as exc:
        return jsonify({'error': str(exc)}), 404
