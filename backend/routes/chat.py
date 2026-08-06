import time
import json
from datetime import date
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ulid import ULID
from backend.db.connection import get_db, row_to_dict
from backend.chat_day import day_key_for
from backend.ai import priority
from backend.ai.provider import is_ai_configured
from backend.ai.chat_title import generate_conversation_title
from backend.delegate import chat as delegate_chat
from backend.todo_recurrence import VALID_LISTS

bp = Blueprint('chat', __name__, url_prefix='/api/chat')


@bp.get('/conversations')
def list_conversations():
    rows = get_db().execute(
        'SELECT * FROM conversations WHERE writing_project_id IS NULL AND idea_id IS NULL ORDER BY updated_at DESC'
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/today')
def get_today():
    """The current chat day's conversation with its messages, or null if none yet.

    `mode` is a leftover of the retired Web Search tab, which kept its own
    conversation per day. Only 'chat' is reachable now; the parameter stays so
    the old rows remain addressable.
    """
    mode = request.args.get('mode', 'chat')
    db = get_db()
    row = db.execute(
        'SELECT * FROM conversations WHERE day_key=? AND writing_project_id IS NULL AND idea_id IS NULL AND mode=?',
        (day_key_for(), mode),
    ).fetchone()
    if not row:
        return jsonify(None)
    conv = row_to_dict(row)
    msgs = db.execute(
        'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (conv['id'],)
    ).fetchall()
    conv['messages'] = [row_to_dict(m) for m in msgs]
    return jsonify(conv)


@bp.get('/journal-conversations')
def journal_conversations():
    """Past chat days for the Journal feed (excludes the current live day).

    Both 'chat' and the retired 'websearch' mode qualify: those days already
    happened, and dropping them from the feed would erase history rather than
    retire a feature.
    """
    rows = get_db().execute(
        '''SELECT c.id, c.title, c.day_key, c.mode, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM messages m
                   WHERE m.conversation_id = c.id AND m.role IN ('user', 'assistant')) AS message_count
           FROM conversations c
           WHERE c.day_key IS NOT NULL AND c.day_key < ? AND c.writing_project_id IS NULL AND c.idea_id IS NULL
             AND EXISTS (SELECT 1 FROM messages m2
                         WHERE m2.conversation_id = c.id AND m2.role IN ('user', 'assistant'))
           ORDER BY c.day_key DESC''',
        (day_key_for(),),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/conversations/<id>')
def get_conversation(id):
    db = get_db()
    row = db.execute('SELECT * FROM conversations WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify(None)
    conv = row_to_dict(row)
    msgs = db.execute(
        'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (id,)
    ).fetchall()
    conv['messages'] = [row_to_dict(m) for m in msgs]
    return jsonify(conv)


@bp.post('/conversations')
def create_conversation():
    """Find-or-create the current chat day's conversation. Titles stay NULL
    until the nightly title job fills them in. `mode` defaults to 'chat' —
    see get_today for why the column outlived the tab."""
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'chat')
    db = get_db()
    dk = day_key_for()
    existing = db.execute(
        'SELECT id FROM conversations WHERE day_key=? AND writing_project_id IS NULL AND idea_id IS NULL AND mode=?',
        (dk, mode),
    ).fetchone()
    if existing:
        return jsonify({'id': existing['id']}), 200
    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO conversations(id, title, day_key, mode, created_at, updated_at) VALUES (?,?,?,?,?,?)',
        (id, None, dk, mode, now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/conversations/<id>/generate-title')
def generate_title(id):
    """Synchronously (re)generate an AI title for one conversation."""
    db = get_db()
    msgs = db.execute(
        'SELECT role, content, metadata FROM messages WHERE conversation_id=? ORDER BY created_at',
        (id,),
    ).fetchall()
    title = generate_conversation_title([dict(m) for m in msgs])
    if not title:
        return jsonify({'title': None})
    db.execute('UPDATE conversations SET title=? WHERE id=?', (title, id))
    db.commit()
    return jsonify({'title': title})


@bp.patch('/conversations/<id>/title')
def update_title(id):
    body = request.json or {}
    title = body.get('title', '')
    get_db().execute(
        'UPDATE conversations SET title=?, updated_at=? WHERE id=?',
        (title, int(time.time()), id),
    )
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/conversations/<id>')
def delete_conversation(id):
    get_db().execute('DELETE FROM conversations WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.post('/conversations/<id>/messages')
def add_message(id):
    body = request.json or {}
    msg_id = str(ULID())
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at) VALUES (?,?,?,?,?,?)',
        (msg_id, id, body.get('role'), body.get('content'), body.get('metadata'), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, id))
    db.commit()
    return jsonify({'id': msg_id}), 201


@bp.post('/briefing/run')
def run_briefing_now():
    """Generate today's overnight briefing on demand (also used by the daemon)."""
    from backend.briefing_job import run_briefing
    from backend.ai.llm import EmptyCompletion
    try:
        result = run_briefing(force=True)
    except EmptyCompletion as e:
        return jsonify({'error': f'The model returned no usable briefing ({e}). '
                                 'Try a different chat/briefing model.'}), 502
    if result is None:
        return jsonify({'error': 'AI provider not configured or nothing to brief'}), 400
    return jsonify(result)


def _cross_off(db, item: dict, now: int) -> None:
    """Cross off one plan item, completing its linked row when it has one.

    An unlinked item never becomes a `todos` row — that's the whole point of the
    daily plan — but it still logs an event so the day's work shows up in the
    Journal feed alongside everything else that got finished."""
    from backend.routes.tasks import (
        _log_event, complete_daily_task, complete_todo_row,
    )

    linked_id = item.get('linkedId')
    linked_type = item.get('linkedType')
    if linked_id and linked_type == 'todo':
        complete_todo_row(db, linked_id, now)
    elif linked_id and linked_type == 'daily':
        complete_daily_task(db, linked_id, date.fromtimestamp(now).isoformat(), now)
    else:
        _log_event(db, 'todo_completed', item['title'], None, item.get('list'), None)


def _apply_briefing_decision(db, item: dict, decision: dict, now: int) -> bool:
    """Resolve one plan item in place. Returns True if a todo row was inserted."""
    from backend.routes.tasks import _parse_priority, _parse_due
    from backend.todo_recurrence import VALID_LISTS

    action = decision.get('action')
    if action == 'reject':
        item['status'] = 'rejected'
        item['resolvedAt'] = now
        return False
    if action == 'done':
        _cross_off(db, item, now)
        item['status'] = 'done'
        item['resolvedAt'] = now
        return False

    # Already on one of the user's lists — there is nothing to add. Left pending
    # so it can still be crossed off.
    if item.get('linkedId'):
        return False

    # Accepting may carry inline edits from the card.
    title = (decision.get('title') if 'title' in decision else item.get('title')) or ''
    title = title.strip()
    if not title:
        return False
    if 'priority' in decision:
        priority, err = _parse_priority(decision.get('priority'))
        if err:
            priority = item.get('priority') or 3
    else:
        priority = item.get('priority') or 3
    if 'due' in decision:
        due, err = _parse_due(decision.get('due'))
        if err:
            due = item.get('due')
    else:
        due = item.get('due')
    todo_list = decision.get('list', item.get('list')) or 'todo'
    if todo_list not in VALID_LISTS:
        todo_list = 'todo'

    item.update({'title': title, 'priority': priority, 'due': due, 'list': todo_list})

    # Re-check for a same-titled open todo at accept time: the list may have
    # moved on since the briefing was written. Link to it and stay pending
    # rather than resolving the card — a twin you can cross off is more useful
    # than a dead "already on your list" row.
    dupe = db.execute(
        'SELECT id, title FROM todos WHERE done=0 AND lower(title)=? AND id!=?',
        (title.lower(), item['id']),
    ).fetchone()
    if dupe:
        item['linkedType'] = 'todo'
        item['linkedId'] = dupe['id']
        item['linkedTitle'] = dupe['title']
        return False

    # The proposal's own id becomes the todo id, so a double-accept is a no-op.
    db.execute(
        'INSERT OR IGNORE INTO todos(id, title, done, list, notes, due, repeat_interval,'
        ' repeat_unit, priority, created_at, updated_at) VALUES (?,?,0,?,?,?,?,?,?,?,?)',
        (item['id'], title, todo_list, None, due, None, None, priority, now, now),
    )
    item['status'] = 'accepted'
    item['resolvedAt'] = now
    return True


@bp.post('/briefing/<message_id>/todos')
def decide_briefing_todos(message_id):
    """Resolve items on the briefing's plan for the day: cross one off (`done`),
    dismiss it (`reject`), or add it to the to-do list for later (`accept`,
    optionally with inline edits). Decisions are written back into the message
    metadata so the chat cards keep the day's record across reloads."""
    body = request.json or {}
    decisions = body.get('decisions')
    if not isinstance(decisions, list) or not decisions:
        return jsonify({'error': 'decisions required'}), 400

    db = get_db()
    row = db.execute('SELECT metadata FROM messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    meta = json.loads(row['metadata']) if row['metadata'] else {}
    proposed = meta.get('proposedTodos')
    if not isinstance(proposed, list):
        return jsonify({'error': 'message has no proposed to-dos'}), 400

    by_id = {i.get('id'): i for i in proposed if isinstance(i, dict)}
    now = int(time.time())
    created = 0
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        item = by_id.get(decision.get('id'))
        # Only pending proposals are actionable — a resolved card can't flip.
        if not item or item.get('status') != 'pending':
            continue
        if _apply_briefing_decision(db, item, decision, now):
            created += 1

    meta['proposedTodos'] = proposed
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), message_id))
    db.commit()
    return jsonify({'proposedTodos': proposed, 'created': created})


@bp.post('/save-calendar')
def save_calendar():
    body = request.json or {}
    now = int(time.time())
    id = str(ULID())
    tags = body.get('tags', [])
    db = get_db()
    db.execute(
        'INSERT INTO calendar_events(id, title, description, date, time, tags, created_at) VALUES (?,?,?,?,?,?,?)',
        (id, body.get('title', ''), body.get('description', ''),
         body.get('date', ''), body.get('time'), json.dumps(tags), now),
    )
    if body.get('messageId'):
        _update_message_metadata(db, body['messageId'], 'savedAsCalendar', id)
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/save-calories')
def save_calories():
    body = request.json or {}
    description = (body.get('description') or '').strip()
    if not description:
        return jsonify({'error': 'description required'}), 400
    calories = body.get('calories')
    if isinstance(calories, bool) or not isinstance(calories, int) or not (0 <= calories <= 20000):
        return jsonify({'error': 'calories must be an integer from 0 to 20000'}), 400

    now = int(time.time())
    id = str(ULID())
    day = body.get('date') or date.today().isoformat()
    db = get_db()
    db.execute(
        'INSERT INTO calorie_logs(id, date, description, calories, created_at) VALUES (?,?,?,?,?)',
        (id, day, description, calories, now),
    )
    if body.get('messageId'):
        _update_message_metadata(db, body['messageId'], 'savedAsCalories', id)
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/save-task')
def save_task():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    todo_list = body.get('list') or 'todo'
    if todo_list not in VALID_LISTS:
        return jsonify({'error': f'list must be one of {", ".join(VALID_LISTS)}'}), 400

    now = int(time.time())
    id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO todos(id, title, done, list, notes, due, repeat_interval,'
        ' repeat_unit, priority, created_at, updated_at) VALUES (?,?,0,?,?,?,?,?,?,?,?)',
        (id, title, todo_list, None, None, None, None, 3, now, now),
    )
    if body.get('messageId'):
        _update_message_metadata(db, body['messageId'], 'savedAsTask', id)
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/stream')
def stream():
    """The Chat tab's one streaming endpoint.

    Four event kinds go out, all under SSE's single `data:` framing:
    `{tool: ...}` as each delegate tool call finishes, `{thinking: ...}` and
    `{content: ...}` as the reply streams, and one `{done: true, steps,
    sources, proposals}` before `[DONE]`. The browser persists `steps`/`sources`
    onto the assistant message and turns `proposals` into confirm cards — the
    live events are gone after a reload, so the same trace has to arrive twice.
    """
    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400
    body = request.json or {}
    messages = body.get('messages', [])
    system_prompt = body.get('systemPrompt', '')

    # Acquired here, in the view, rather than inside generate(): the generator
    # body does not run until Werkzeug pulls the first item, so acquiring there
    # would leave the window between "user pressed Enter" and "first token"
    # looking idle to background work. Released in the generator's finally,
    # which also runs on GeneratorExit when the client disconnects mid-stream.
    # One mark spans the whole turn, delegate sub-loop included — the user is
    # waiting on all of it, the same way ideas.discuss holds one across both its
    # blocking gather and its streamed answer.
    token = priority.begin('chat.stream')

    def generate():
        try:
            # A caller-supplied systemPrompt means this is not the Chat tab —
            # it's the voice listener, a task nudge or the morning check-in,
            # none of which have a card to confirm anything on.
            for kind, payload in delegate_chat.stream_reply(
                messages, system_prompt, delegate=not system_prompt
            ):
                if kind == 'step':
                    yield f'data: {json.dumps(payload)}\n\n'
                elif kind == 'done':
                    yield f'data: {json.dumps({"done": True, **payload})}\n\n'
                else:
                    yield f'data: {json.dumps({kind: payload})}\n\n'
            yield 'data: [DONE]\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
        finally:
            # Must not yield here — that would raise "generator ignored
            # GeneratorExit". priority.end only touches a dict under a lock.
            priority.end(token)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )


def _update_message_metadata(db, message_id: str, key: str, value: str) -> None:
    row = db.execute('SELECT metadata FROM messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        return
    meta = json.loads(row['metadata']) if row['metadata'] else {}
    meta[key] = value
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), message_id))
