"""The overnight-briefing sweep body.

A plain function (no Flask request, no thread) so it can be called directly by
the scheduler thread, by the manual-trigger route, and by tests. It find-or-
creates today's chat and drops in the AI briefing as the first assistant
message. The to-dos it suggests are *proposals*: they ride along in the
message metadata and only become real todos when the user accepts them in the
chat (see `POST /api/chat/briefing/<message_id>/todos`).
"""
import json
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.chat_day import day_key_for
from backend.ai.provider import is_ai_configured
from backend.ai.briefing import gather_briefing_context, generate_briefing, MAX_BRIEFING_TODOS
from backend.routes.tasks import _parse_priority, _parse_due
from backend.todo_recurrence import VALID_LISTS


def _find_or_create_day_conversation(db, day_key: str, now: int) -> str:
    row = db.execute(
        'SELECT id FROM conversations WHERE day_key=? AND writing_project_id IS NULL',
        (day_key,),
    ).fetchone()
    if row:
        return row['id']
    conv_id = str(ULID())
    db.execute(
        'INSERT INTO conversations(id, title, day_key, created_at, updated_at) VALUES (?,?,?,?,?)',
        (conv_id, None, day_key, now, now),
    )
    return conv_id


def _has_briefing(db, conv_id: str) -> bool:
    rows = db.execute(
        "SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant'",
        (conv_id,),
    ).fetchall()
    for r in rows:
        if not r['metadata']:
            continue
        try:
            if json.loads(r['metadata']).get('briefing'):
                return True
        except (ValueError, TypeError):
            continue
    return False


def _propose_todos(db, proposed: list) -> list[dict]:
    """Validate/clamp, drop titles that already exist as open todos, and cap.
    Returns pending proposals for the message metadata — nothing is inserted.
    Each carries its own id so accepting is idempotent."""
    existing = {
        r['title'].strip().lower()
        for r in db.execute('SELECT title FROM todos WHERE done=0').fetchall()
    }
    items: list[dict] = []
    for item in proposed:
        if len(items) >= MAX_BRIEFING_TODOS:
            break
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or '').strip()
        if not title or title.lower() in existing:
            continue

        todo_list = item.get('list', 'todo')
        if todo_list not in VALID_LISTS:
            todo_list = 'todo'
        priority, err = _parse_priority(item.get('priority'))
        if err:
            priority = 3
        due, err = _parse_due(item.get('due'))
        if err:
            due = None

        items.append({
            'id': str(ULID()),
            'title': title,
            'list': todo_list,
            'priority': priority,
            'due': due,
            'status': 'pending',
        })
        existing.add(title.lower())
    return items


def run_briefing(now: int | None = None, force: bool = False) -> dict | None:
    """Generate and store today's briefing. Returns a summary dict, or None if it
    was skipped (AI unconfigured, or a briefing already exists and not forced)."""
    if not is_ai_configured():
        return None
    now = now if now is not None else int(time.time())
    db = get_db()
    day_key = day_key_for(now)
    conv_id = _find_or_create_day_conversation(db, day_key, now)

    if _has_briefing(db, conv_id) and not force:
        db.commit()  # persist the conversation if we just created it
        return None

    context = gather_briefing_context(now)
    result = generate_briefing(context)
    briefing = (result.get('briefing') or '').strip()
    if not briefing:
        db.commit()
        return None

    proposed = _propose_todos(db, result.get('todos', []))
    message_id = str(ULID())
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (message_id, conv_id, 'assistant', briefing,
         json.dumps({'briefing': True, 'proposedTodos': proposed}), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conv_id))
    db.commit()
    return {
        'conversationId': conv_id,
        'messageId': message_id,
        'briefing': briefing,
        'todosProposed': len(proposed),
    }
