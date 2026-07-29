"""The overnight-briefing sweep body.

A plain function (no Flask request, no thread) so it can be called directly by
the scheduler thread, by the manual-trigger route, and by tests. It find-or-
creates today's chat and drops in the AI briefing as the first assistant
message. The to-dos it suggests are *today's plan*, not backlog: they ride
along in the message metadata and are crossed off in the chat itself. Only an
explicit "add to to-dos" ever writes a `todos` row (see
`POST /api/chat/briefing/<message_id>/todos`). An item that restates something
already on the user's lists carries a link to that row, so crossing it off here
completes the real one.
"""
import json
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.chat_day import day_key_for
from backend.ai.provider import is_ai_configured
from backend.ai.briefing import (
    _pending_daily_tasks,
    gather_briefing_context,
    generate_briefing,
    MAX_BRIEFING_TODOS,
)
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


def _twin_lookup(db, today: str) -> dict[str, tuple[str, str]]:
    """`{lowercased title: (linkedType, id)}` for everything a proposal may be a
    twin of: open todos and daily tasks still pending today. Todos win a title
    collision — they're the list the briefing draws most of its plan from."""
    lookup: dict[str, tuple[str, str]] = {}
    for r in _pending_daily_tasks(db, today):
        lookup[r['title'].strip().lower()] = ('daily', r['id'])
    for r in db.execute('SELECT id, title FROM todos WHERE done=0').fetchall():
        lookup[r['title'].strip().lower()] = ('todo', r['id'])
    return lookup


def _propose_todos(db, proposed: list, today: str) -> list[dict]:
    """Validate/clamp, link each item to its existing twin where there is one,
    and cap. Returns pending proposals for the message metadata — nothing is
    inserted. Each carries its own id so accepting is idempotent.

    Items that restate an open todo or a pending daily task are deliberately
    *kept* rather than dropped: today's plan should show the whole day, and the
    link is what lets crossing one off here cross off the real row too."""
    twins = _twin_lookup(db, today)
    seen: set[str] = set()
    items: list[dict] = []
    for item in proposed:
        if len(items) >= MAX_BRIEFING_TODOS:
            break
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or '').strip()
        # Only same-batch repeats are dropped; twins of existing rows are linked.
        if not title or title.lower() in seen:
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

        # Trust the model's declared link first — it sees the same titles we do
        # and can match on meaning. Fall back to the proposal's own title so a
        # verbatim restatement links even when the model forgot to say so.
        declared = item.get('linkedTitle')
        declared = declared.strip() if isinstance(declared, str) else ''
        twin = twins.get(declared.lower()) if declared else None
        matched_title = declared
        if twin is None:
            twin = twins.get(title.lower())
            matched_title = title if twin else ''

        items.append({
            'id': str(ULID()),
            'title': title,
            'list': todo_list,
            'priority': priority,
            'due': due,
            'status': 'pending',
            'linkedType': twin[0] if twin else None,
            'linkedId': twin[1] if twin else None,
            'linkedTitle': matched_title if twin else None,
            'resolvedAt': None,
        })
        seen.add(title.lower())
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

    proposed = _propose_todos(db, result.get('todos', []), context['today'])
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
