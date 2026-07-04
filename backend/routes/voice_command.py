import re
import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.ai.commands import parse_voice_command
from backend.db.connection import get_db

bp = Blueprint('voice_command', __name__, url_prefix='/api/voice-command')

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TIME_RE = re.compile(r'^\d{2}:\d{2}$')


def _create_todo(todo: dict) -> str | None:
    title = (todo.get('title') or '').strip()
    if not title:
        return None
    now = int(time.time())
    todo_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO todos(id, title, done, created_at, updated_at) VALUES (?,?,0,?,?)',
        (todo_id, title, now, now),
    )
    db.commit()
    return todo_id


def _create_event(event: dict) -> str | None:
    title = (event.get('title') or '').strip()
    date_str = (event.get('date') or '').strip()
    if not title or not _DATE_RE.match(date_str):
        return None
    time_str = (event.get('time') or '').strip()
    if not _TIME_RE.match(time_str):
        time_str = None
    now = int(time.time())
    event_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO calendar_events(id, title, description, date, time, end_time, tags, journal_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (event_id, title, event.get('description'), date_str, time_str, None, None, None, now),
    )
    db.commit()
    return event_id


def _create_journal(journal: dict) -> str | None:
    content = (journal.get('content') or '').strip()
    if not content:
        return None
    from backend.routes.journal import (
        _generate_metadata_bg, _notify_subscribers, _polish_bg, _sync_embeddings_bg,
    )
    now = int(time.time())
    entry_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO journal_entries(id, content, raw_content, title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (entry_id, content, content, None, None, now, now),
    )
    db.commit()
    _notify_subscribers(entry_id)
    _sync_embeddings_bg(entry_id)
    _polish_bg(entry_id, content)
    _generate_metadata_bg(entry_id, content)
    return entry_id


@bp.post('')
def handle_command():
    """Parse a transcribed voice command with the LLM and execute the resulting action.

    Body: {"messages": [{"role": "user"|"assistant", "content": "..."}, ...]}
    The conversation may include earlier clarifying questions from the assistant.

    Response: {"status": "done"|"clarify"|"none", "action": ..., "speak": ...}
    """
    body = request.json or {}
    messages = body.get('messages') or []
    if not isinstance(messages, list) or not any(
        isinstance(m, dict) and m.get('role') == 'user' and (m.get('content') or '').strip()
        for m in messages
    ):
        return jsonify({'error': 'messages with at least one user turn required'}), 400

    result = parse_voice_command(messages)
    action = result.get('action')
    speak = (result.get('speak') or '').strip()

    if action == 'clarify':
        return jsonify({
            'status': 'clarify',
            'action': action,
            'speak': speak or 'Could you give me a bit more detail?',
        })

    created_id = None
    if action == 'create_todo':
        created_id = _create_todo(result.get('todo') or {})
        fallback = 'Added the todo.'
    elif action == 'create_event':
        created_id = _create_event(result.get('event') or {})
        fallback = 'Added the event.'
    elif action == 'create_journal':
        created_id = _create_journal(result.get('journal') or {})
        fallback = 'Saved the journal entry.'
    else:
        return jsonify({
            'status': 'none',
            'action': 'none',
            'speak': speak or "Sorry, I didn't understand that command.",
        })

    if not created_id:
        return jsonify({
            'status': 'none',
            'action': action,
            'speak': "I understood the command but couldn't extract the details. Please try again.",
        })

    return jsonify({
        'status': 'done',
        'action': action,
        'id': created_id,
        'speak': speak or fallback,
        'details': result.get('todo') or result.get('event') or result.get('journal'),
    })
