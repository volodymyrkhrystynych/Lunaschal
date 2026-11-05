"""Saying something to the user: a chat message first, then a push.

The message is written and committed before any push is attempted, so the
chat is the record of everything the coach said and the fallback when there
is no phone to push to — APNs not configured, no device registered, Apple
unreachable, a token gone stale. A push failure is reported, never raised.
"""
import json
import logging
import time

from ulid import ULID

from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.push import notify
from backend.push.payload import alert

logger = logging.getLogger(__name__)


def post_message(content: str, metadata: dict, now: int) -> tuple[str, str]:
    """An assistant message in today's chat. Returns (conversation, message)."""
    from backend.briefing_job import find_or_create_day_conversation
    db = get_db()
    conv_id = find_or_create_day_conversation(db, day_key_for(now), now)
    message_id = str(ULID())
    db.execute('INSERT INTO messages (id, conversation_id, role, content, metadata, created_at) '
               "VALUES (?,?,'assistant',?,?,?)", (message_id, conv_id, content, json.dumps(metadata), now))
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conv_id))
    db.commit()
    return conv_id, message_id


def push(title: str, body: str, *, category: str, data: dict, collapse_id: str | None = None,
         now: int | None = None, client=None) -> dict:
    """Send one alert to the phones that turned reminders on."""
    return notify.send(alert(title, body, category=category, data=data), collapse_id=collapse_id,
                       coach_only=True, now=now, client=client)


def deliver(content: str, metadata: dict, *, title: str, body: str, category: str,
            data: dict | None = None, collapse_id: str | None = None, now: int | None = None,
            client=None) -> dict:
    now = int(now if now is not None else time.time())
    conv_id, message_id = post_message(content, metadata, now)
    coach = metadata.get('coach') or {}
    payload = {'kind': coach.get('kind'), 'day': coach.get('day'), 'messageId': message_id,
               'conversationId': conv_id, **(data or {})}
    try:
        sent = push(title, body, category=category, data=payload, collapse_id=collapse_id, now=now, client=client)
    except Exception as exc:
        logger.exception('Push failed after the message was saved')
        sent = {'pushed': 0, 'errors': [str(exc)[:200]], 'configured': True}
    return {'conversationId': conv_id, 'messageId': message_id, **sent}
