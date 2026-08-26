import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, mapping_to_dict, row_to_dict
from backend.day_boundary import day_bounds, day_key_for
from backend.todo_recurrence import (
    next_due, normalize_list, parse_priority as _parse_priority,
    parse_repeat as _parse_repeat,
)

bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

MAX_TASKS = 4


def _today() -> str:
    return day_key_for()


def _log_event(
    db,
    kind: str,
    title: str,
    ref_id: str | None = None,
    task_list: str | None = None,
    detail: str | None = None,
) -> None:
    """Append a task lifecycle event (surfaced in the Journal feed).

    `task_list` records which list the task came from ('todo'/'chores'/'archive'
    for todos, 'daily' for daily tasks) so the notification can show it. `detail`
    snapshots the task's notes so the expanded notification can recall what it was
    even after the task is gone.
    """
    db.execute(
        'INSERT INTO task_events(id, kind, title, ref_id, task_list, detail, created_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (str(ULID()), kind, title, ref_id, task_list, detail or None, int(time.time())),
    )


@bp.get('')
def list_tasks():
    today = _today()
    db = get_db()
    rows = db.execute(
        '''
        SELECT t.id, t.title, t.position, t.created_at, t.updated_at,
               CASE WHEN c.id IS NOT NULL THEN 1 ELSE 0 END AS done
        FROM daily_tasks t
        LEFT JOIN daily_task_completions c ON c.task_id = t.id AND c.date = ?
        ORDER BY t.position
        ''',
        (today,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('')
def create_task():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400

    db = get_db()
    count = db.execute('SELECT COUNT(*) FROM daily_tasks').fetchone()[0]
    if count >= MAX_TASKS:
        return jsonify({'error': f'max {MAX_TASKS} tasks allowed'}), 400

    position = count + 1
    now = int(time.time())
    task_id = str(ULID())
    db.execute(
        'INSERT INTO daily_tasks(id, title, position, created_at, updated_at) VALUES (?,?,?,?,?)',
        (task_id, title, position, now, now),
    )
    db.commit()
    return jsonify({'id': task_id}), 201


@bp.patch('/<task_id>')
def update_task(task_id):
    body = request.json or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400

    db = get_db()
    db.execute(
        'UPDATE daily_tasks SET title=?, updated_at=? WHERE id=?',
        (title, int(time.time()), task_id),
    )
    db.commit()
    return jsonify({'success': True})


@bp.post('/reorder')
def reorder_tasks():
    body = request.json or {}
    order = body.get('order', [])
    if not isinstance(order, list):
        return jsonify({'error': 'order must be a list of ids'}), 400

    db = get_db()
    now = int(time.time())
    for i, task_id in enumerate(order):
        db.execute(
            'UPDATE daily_tasks SET position=?, updated_at=? WHERE id=?',
            (i + 1, now, task_id),
        )
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<task_id>')
def delete_task(task_id):
    db = get_db()
    row = db.execute(
        'SELECT position, title FROM daily_tasks WHERE id=?', (task_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    deleted_pos = row['position']
    _log_event(db, 'task_deleted', row['title'], task_id, 'daily')
    db.execute('DELETE FROM daily_tasks WHERE id=?', (task_id,))
    db.execute(
        'UPDATE daily_tasks SET position=position-1, updated_at=? WHERE position > ?',
        (int(time.time()), deleted_pos),
    )
    db.commit()
    return jsonify({'success': True})


def complete_daily_task(db, task_id: str, today: str, now: int) -> bool:
    """Record today's completion of a daily task. Returns True if this call was
    the one that recorded it (a repeat is a no-op). Does not commit — the caller
    owns the transaction. Shared with the chat's daily-plan cards."""
    cur = db.execute(
        'INSERT OR IGNORE INTO daily_task_completions(id, task_id, date, created_at)'
        ' VALUES (?,?,?,?)',
        (str(ULID()), task_id, today, now),
    )
    # Only log when this call actually recorded a completion (not a repeat POST).
    if not cur.rowcount:
        return False
    task = db.execute(
        'SELECT title FROM daily_tasks WHERE id=?', (task_id,)
    ).fetchone()
    if task:
        _log_event(db, 'daily_completed', task['title'], task_id, 'daily')
    return True


@bp.post('/<task_id>/complete')
def complete_task(task_id):
    db = get_db()
    complete_daily_task(db, task_id, _today(), int(time.time()))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<task_id>/complete')
def uncomplete_task(task_id):
    today = _today()
    db = get_db()
    db.execute(
        'DELETE FROM daily_task_completions WHERE task_id=? AND date=?',
        (task_id, today),
    )
    # Retract today's completion notification so toggling off leaves no phantom.
    db.execute(
        "DELETE FROM task_events WHERE kind='daily_completed' AND ref_id=?"
        " AND created_at >= ?",
        (task_id, day_bounds(today)[0]),
    )
    db.commit()
    return jsonify({'success': True})


# --- Todos (one-off items, unlike daily tasks they don't reset each day) ---

_TODO_COLS = (
    'id, title, done, completed_at, list, notes, due, '
    'repeat_interval, repeat_unit, priority, created_at, updated_at'
)


def _parse_due(value):
    """Returns (unix_int_or_None, error_or_None)."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int):
        return None, 'due must be a unix timestamp'
    return value, None


@bp.get('/todos')
def list_todos():
    db = get_db()
    list_filter = request.args.get('list')
    if list_filter is not None:
        list_filter, err = normalize_list(list_filter)
        if err:
            return jsonify({'error': err}), 400
    query = f'SELECT {_TODO_COLS} FROM todos'
    params = []
    if list_filter:
        query += ' WHERE list=?'
        params.append(list_filter)
    query += ' ORDER BY done, created_at'
    rows = db.execute(query, params).fetchall()
    todos = [row_to_dict(r) for r in rows]
    for t in todos:
        t['done'] = bool(t['done'])  # SQLite stores 0/1; the API contract is a boolean
    return jsonify(todos)


def _insert_todo_row(db, body: dict) -> tuple[str | None, str | None]:
    """Validate and insert a permanent todos row. Returns (id, None) on success
    or (None, error). Shared by create_todo and promote_chat_todo so there is
    exactly one place that decides what a valid todo looks like."""
    title = (body.get('title') or '').strip()
    if not title:
        return None, 'title required'
    todo_list, err = normalize_list(body.get('list'))
    if err:
        return None, err
    notes = (body.get('notes') or '').strip() or None
    due, err = _parse_due(body.get('due'))
    if err:
        return None, err
    repeat, err = _parse_repeat(body.get('repeatInterval'), body.get('repeatUnit'))
    if err:
        return None, err
    priority, err = _parse_priority(body.get('priority'))
    if err:
        return None, err

    now = int(time.time())
    # Accept a client-supplied ULID so an offline-queued create replays
    # idempotently (INSERT OR IGNORE makes a duplicate a no-op).
    todo_id = body.get('id') or str(ULID())
    db.execute(
        'INSERT OR IGNORE INTO todos(id, title, done, list, notes, due, repeat_interval, repeat_unit, priority, created_at, updated_at)'
        ' VALUES (?,?,0,?,?,?,?,?,?,?,?)',
        (todo_id, title, todo_list, notes, due, repeat[0], repeat[1], priority, now, now),
    )
    return todo_id, None


@bp.post('/todos')
def create_todo():
    db = get_db()
    todo_id, err = _insert_todo_row(db, request.json or {})
    if err:
        return jsonify({'error': err}), 400
    db.commit()
    return jsonify({'id': todo_id}), 201


def complete_todo_row(db, todo_id: str, now: int) -> bool:
    """Mark a todo done — or, when it repeats, advance it to its next occurrence.

    Returns True if this call recorded a completion; re-completing an already-done
    one-off is a no-op so it can't log a second event. Does not commit — the
    caller owns the transaction. Shared with the chat's daily-plan cards, which
    must complete a linked todo exactly the way the Tasks view does."""
    row = db.execute(
        'SELECT title, done, list, notes, due, repeat_interval, repeat_unit'
        ' FROM todos WHERE id=?',
        (todo_id,),
    ).fetchone()
    if row is None:
        return False

    if row['repeat_interval'] and row['repeat_unit']:
        # Repeating todo: completing it schedules the next occurrence instead of
        # marking it done. Each occurrence is a real completion, so always log it.
        db.execute(
            'UPDATE todos SET done=0, completed_at=NULL, due=?, updated_at=? WHERE id=?',
            (
                next_due(row['due'], row['repeat_interval'], row['repeat_unit'], now),
                now,
                todo_id,
            ),
        )
    else:
        db.execute(
            # COALESCE keeps the original completion time if it was already done.
            'UPDATE todos SET done=1, completed_at=COALESCE(completed_at, ?),'
            ' updated_at=? WHERE id=?',
            (now, now, todo_id),
        )
        # Only log when it wasn't already done (avoids a duplicate event).
        if row['done']:
            return False

    _log_event(db, 'todo_completed', row['title'], todo_id, row['list'], row['notes'])
    return True


def _uncomplete_todo_row(db, todo_id: str, now: int) -> None:
    """Reopen a todo and retract its completion notification."""
    db.execute(
        'UPDATE todos SET done=0, completed_at=NULL, updated_at=? WHERE id=?',
        (now, todo_id),
    )
    db.execute(
        "DELETE FROM task_events WHERE kind='todo_completed' AND ref_id=?",
        (todo_id,),
    )


@bp.patch('/todos/<todo_id>')
def update_todo(todo_id):
    body = request.json or {}
    db = get_db()
    row = db.execute(
        'SELECT title, done, list, notes, due, repeat_interval, repeat_unit'
        ' FROM todos WHERE id=?',
        (todo_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'not found'}), 404

    fields = []
    values = []
    if 'title' in body:
        title = (body.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'title required'}), 400
        fields.append('title=?')
        values.append(title)
    if 'list' in body:
        todo_list, err = normalize_list(body.get('list'))
        if err:
            return jsonify({'error': err}), 400
        fields.append('list=?')
        values.append(todo_list)
    if 'notes' in body:
        fields.append('notes=?')
        values.append((body.get('notes') or '').strip() or None)
    if 'priority' in body:
        priority, err = _parse_priority(body.get('priority'))
        if err:
            return jsonify({'error': err}), 400
        fields.append('priority=?')
        values.append(priority)
    if 'due' in body:
        due, err = _parse_due(body.get('due'))
        if err:
            return jsonify({'error': err}), 400
        fields.append('due=?')
        values.append(due)
    if 'repeatInterval' in body or 'repeatUnit' in body:
        repeat, err = _parse_repeat(body.get('repeatInterval'), body.get('repeatUnit'))
        if err:
            return jsonify({'error': err}), 400
        fields.append('repeat_interval=?')
        values.append(repeat[0])
        fields.append('repeat_unit=?')
        values.append(repeat[1])
    # Applied after the field UPDATE below, so completing alongside an edit
    # (e.g. a new `due` on a repeating todo) advances from the edited values.
    completion_change = None
    if 'done' in body:
        completion_change = 'complete' if body['done'] else 'uncomplete'
    if not fields and completion_change is None:
        return jsonify({'error': 'nothing to update'}), 400

    now = int(time.time())
    if fields:
        fields.append('updated_at=?')
        values.extend([now, todo_id])
        db.execute(f'UPDATE todos SET {", ".join(fields)} WHERE id=?', values)
    if completion_change == 'complete':
        complete_todo_row(db, todo_id, now)
    elif completion_change == 'uncomplete':
        _uncomplete_todo_row(db, todo_id, now)
    db.commit()
    return jsonify({'success': True})


@bp.delete('/todos/<todo_id>')
def delete_todo(todo_id):
    db = get_db()
    row = db.execute(
        'SELECT title, done, list, notes FROM todos WHERE id=?', (todo_id,)
    ).fetchone()
    # Log removals of still-active items only — deleting an already-done todo is
    # cleanup, not a "removed from my list" event.
    if row and not row['done']:
        _log_event(
            db, 'task_deleted', row['title'], todo_id, row['list'], row['notes']
        )
    db.execute('DELETE FROM todos WHERE id=?', (todo_id,))
    db.commit()
    return jsonify({'success': True})



# --- Chat to-dos (ephemeral, day-scoped items in the Chat tab's bar) ---

_CHAT_TODO_COLS = (
    'id, day_key, title, notes, due, priority, done, completed_at, created_at, updated_at'
)

MAX_CHAT_TODO_ITEMS = 10


def _today_chat_todo_titles(db, today: str) -> set[str]:
    """Lowercased titles already sitting in today's bar. The briefing seed uses
    only this — not _today_taken_titles below — because its whole job is to
    mirror the user's open todos and pending daily tasks into the bar; skipping
    a title just because it's already an open todo would silently drop most of
    the plan. This still prevents a forced re-run (or a same-batch repeat) from
    inserting the same title twice."""
    return {
        r['title'].strip().lower()
        for r in db.execute(
            'SELECT title FROM chat_todos WHERE day_key=?', (today,)
        ).fetchall()
    }


def _today_taken_titles(db, today: str) -> set[str]:
    """Lowercased titles already spoken for today: open todos, pending daily
    tasks, and today's existing chat_todos rows. Used by the live-chat add
    paths (the bulk-add route and the add_todos tool) so a to-do added mid-
    conversation doesn't duplicate something already tracked elsewhere."""
    from backend.ai.briefing import _pending_daily_tasks

    taken = {
        r['title'].strip().lower()
        for r in db.execute('SELECT title FROM todos WHERE done=0').fetchall()
    }
    taken |= {r['title'].strip().lower() for r in _pending_daily_tasks(db, today)}
    taken |= {
        r['title'].strip().lower()
        for r in db.execute(
            'SELECT title FROM chat_todos WHERE day_key=?', (today,)
        ).fetchall()
    }
    return taken


@bp.get('/chat-todos')
def list_chat_todos():
    db = get_db()
    rows = db.execute(
        f'SELECT {_CHAT_TODO_COLS} FROM chat_todos WHERE day_key=? ORDER BY done, created_at',
        (_today(),),
    ).fetchall()
    todos = [row_to_dict(r) for r in rows]
    for t in todos:
        t['done'] = bool(t['done'])
    return jsonify(todos)


@bp.post('/chat-todos')
def add_chat_todos():
    """Instant, no-confirm bulk add — used by both the chat delegate's
    add_todos tool and the nightly briefing seed. A bad item is skipped, not
    fatal: the caller sent several at once and one bad title shouldn't cost
    the rest."""
    body = request.json or {}
    items = body.get('items')
    if not isinstance(items, list) or not items:
        return jsonify({'error': 'items required'}), 400

    db = get_db()
    today = _today()
    taken = _today_taken_titles(db, today)
    now = int(time.time())
    created = []
    for item in items[:MAX_CHAT_TODO_ITEMS]:
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or '').strip()
        if not title or title.lower() in taken:
            continue
        notes = (item.get('notes') or '').strip() or None
        chat_todo_id = str(ULID())
        db.execute(
            'INSERT INTO chat_todos(id, day_key, title, notes, due, priority, done, created_at, updated_at)'
            ' VALUES (?,?,?,?,NULL,3,0,?,?)',
            (chat_todo_id, today, title, notes, now, now),
        )
        taken.add(title.lower())
        row = mapping_to_dict({
            'id': chat_todo_id, 'day_key': today, 'title': title, 'notes': notes,
            'due': None, 'priority': 3, 'done': 0, 'completed_at': None,
            'created_at': now, 'updated_at': now,
        })
        row['done'] = False
        created.append(row)
    db.commit()
    return jsonify(created), 201


def _complete_chat_todo_row(db, chat_todo_id: str, now: int) -> bool:
    """Mark a chat to-do done and log its completion, surfaced in the Journal
    feed the same way a permanent todo's completion is. Returns False if it
    was already done or doesn't exist. Does not commit — caller's job."""
    row = db.execute(
        'SELECT title, done, notes FROM chat_todos WHERE id=?', (chat_todo_id,)
    ).fetchone()
    if row is None or row['done']:
        return False
    db.execute(
        'UPDATE chat_todos SET done=1, completed_at=?, updated_at=? WHERE id=?',
        (now, now, chat_todo_id),
    )
    _log_event(db, 'chat_todo_completed', row['title'], chat_todo_id, 'chat', row['notes'])
    return True


def _uncomplete_chat_todo_row(db, chat_todo_id: str, now: int) -> None:
    """Reopen a chat to-do and retract its completion notification."""
    db.execute(
        'UPDATE chat_todos SET done=0, completed_at=NULL, updated_at=? WHERE id=?',
        (now, chat_todo_id),
    )
    db.execute(
        "DELETE FROM task_events WHERE kind='chat_todo_completed' AND ref_id=?",
        (chat_todo_id,),
    )


@bp.patch('/chat-todos/<chat_todo_id>')
def update_chat_todo(chat_todo_id):
    body = request.json or {}
    db = get_db()
    row = db.execute(
        'SELECT title, done, notes FROM chat_todos WHERE id=?', (chat_todo_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'not found'}), 404

    fields = []
    values = []
    if 'title' in body:
        title = (body.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'title required'}), 400
        fields.append('title=?')
        values.append(title)
    if 'notes' in body:
        fields.append('notes=?')
        values.append((body.get('notes') or '').strip() or None)
    if 'priority' in body:
        priority, err = _parse_priority(body.get('priority'))
        if err:
            return jsonify({'error': err}), 400
        fields.append('priority=?')
        values.append(priority)
    if 'due' in body:
        due, err = _parse_due(body.get('due'))
        if err:
            return jsonify({'error': err}), 400
        fields.append('due=?')
        values.append(due)
    # Applied after the field UPDATE below, matching update_todo's ordering.
    completion_change = None
    if 'done' in body:
        completion_change = 'complete' if body['done'] else 'uncomplete'
    if not fields and completion_change is None:
        return jsonify({'error': 'nothing to update'}), 400

    now = int(time.time())
    if fields:
        fields.append('updated_at=?')
        values.extend([now, chat_todo_id])
        db.execute(f'UPDATE chat_todos SET {", ".join(fields)} WHERE id=?', values)
    if completion_change == 'complete':
        _complete_chat_todo_row(db, chat_todo_id, now)
    elif completion_change == 'uncomplete':
        _uncomplete_chat_todo_row(db, chat_todo_id, now)
    db.commit()
    return jsonify({'success': True})


@bp.delete('/chat-todos/<chat_todo_id>')
def delete_chat_todo(chat_todo_id):
    # No task_events log — dismissing an ephemeral item was never a notable
    # event, same as the accept/reject cards this bar replaced.
    db = get_db()
    db.execute('DELETE FROM chat_todos WHERE id=?', (chat_todo_id,))
    db.commit()
    return jsonify({'success': True})


@bp.post('/chat-todos/<chat_todo_id>/promote')
def promote_chat_todo(chat_todo_id):
    """Move a chat to-do to the permanent list: insert via the same validation
    path create_todo uses, then remove the ephemeral row — one transaction, so
    a rejected payload leaves the source row untouched."""
    db = get_db()
    if not db.execute('SELECT 1 FROM chat_todos WHERE id=?', (chat_todo_id,)).fetchone():
        return jsonify({'error': 'not found'}), 404
    todo_id, err = _insert_todo_row(db, request.json or {})
    if err:
        return jsonify({'error': err}), 400
    db.execute('DELETE FROM chat_todos WHERE id=?', (chat_todo_id,))
    db.commit()
    return jsonify({'id': todo_id}), 201


@bp.get('/events')
def list_task_events():
    """Recent task lifecycle events (completions/deletions) for the Journal feed."""
    try:
        limit = min(max(int(request.args.get('limit', 200)), 1), 1000)
    except (TypeError, ValueError):
        limit = 200
    db = get_db()
    rows = db.execute(
        'SELECT id, kind, title, ref_id, task_list, detail, created_at FROM task_events'
        ' ORDER BY created_at DESC, id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])
