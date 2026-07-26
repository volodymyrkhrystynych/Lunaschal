import time
from datetime import date

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.todo_recurrence import VALID_LISTS, VALID_UNITS, next_due

bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

MAX_TASKS = 4


def _today() -> str:
    return date.today().isoformat()


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


@bp.post('/<task_id>/complete')
def complete_task(task_id):
    today = _today()
    db = get_db()
    cur = db.execute(
        'INSERT OR IGNORE INTO daily_task_completions(id, task_id, date, created_at)'
        ' VALUES (?,?,?,?)',
        (str(ULID()), task_id, today, int(time.time())),
    )
    # Only log when this call actually recorded a completion (not a repeat POST).
    if cur.rowcount:
        task = db.execute(
            'SELECT title FROM daily_tasks WHERE id=?', (task_id,)
        ).fetchone()
        if task:
            _log_event(db, 'daily_completed', task['title'], task_id, 'daily')
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
        (task_id, int(time.mktime(date.today().timetuple()))),
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


def _parse_repeat(interval, unit):
    """Returns ((interval, unit) or (None, None), error_or_None)."""
    if interval is None and unit is None:
        return (None, None), None
    if interval is None or unit is None:
        return None, 'repeatInterval and repeatUnit must be set together'
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        return None, 'repeatInterval must be a positive integer'
    if unit not in VALID_UNITS:
        return None, f'repeatUnit must be one of {", ".join(VALID_UNITS)}'
    return (interval, unit), None


def _parse_priority(value):
    """Returns (int_1_to_5, error_or_None). Absent/None -> default 3 (neutral)."""
    if value is None:
        return 3, None
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 5):
        return None, 'priority must be an integer from 1 to 5'
    return value, None


@bp.get('/todos')
def list_todos():
    db = get_db()
    list_filter = request.args.get('list')
    if list_filter is not None and list_filter not in VALID_LISTS:
        return jsonify({'error': f'list must be one of {", ".join(VALID_LISTS)}'}), 400
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


@bp.post('/todos')
def create_todo():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    todo_list = body.get('list', 'todo')
    if todo_list not in VALID_LISTS:
        return jsonify({'error': f'list must be one of {", ".join(VALID_LISTS)}'}), 400
    notes = (body.get('notes') or '').strip() or None
    due, err = _parse_due(body.get('due'))
    if err:
        return jsonify({'error': err}), 400
    repeat, err = _parse_repeat(body.get('repeatInterval'), body.get('repeatUnit'))
    if err:
        return jsonify({'error': err}), 400
    priority, err = _parse_priority(body.get('priority'))
    if err:
        return jsonify({'error': err}), 400

    now = int(time.time())
    # Accept a client-supplied ULID so an offline-queued create replays
    # idempotently (INSERT OR IGNORE makes a duplicate a no-op).
    todo_id = body.get('id') or str(ULID())
    db = get_db()
    db.execute(
        'INSERT OR IGNORE INTO todos(id, title, done, list, notes, due, repeat_interval, repeat_unit, priority, created_at, updated_at)'
        ' VALUES (?,?,0,?,?,?,?,?,?,?,?)',
        (todo_id, title, todo_list, notes, due, repeat[0], repeat[1], priority, now, now),
    )
    db.commit()
    return jsonify({'id': todo_id}), 201


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
        todo_list = body.get('list')
        if todo_list not in VALID_LISTS:
            return jsonify({'error': f'list must be one of {", ".join(VALID_LISTS)}'}), 400
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
    # 'complete' -> log a completion event; 'uncomplete' -> retract it. Left None
    # when `done` isn't part of this update. Applied after the UPDATE below.
    completion_change = None
    if 'done' in body:
        if body['done']:
            if row['repeat_interval'] and row['repeat_unit']:
                # Repeating todo: completing it schedules the next occurrence
                # instead of marking it done. Each occurrence is a real
                # completion, so always log it.
                now = int(time.time())
                fields.append('done=0')
                fields.append('completed_at=NULL')
                fields.append('due=?')
                values.append(
                    next_due(row['due'], row['repeat_interval'], row['repeat_unit'], now)
                )
                completion_change = 'complete'
            else:
                fields.append('done=1')
                # Keep the original completion time if it was already done
                fields.append('completed_at=COALESCE(completed_at, ?)')
                values.append(int(time.time()))
                # Only log when it wasn't already done (avoids a duplicate event).
                if not row['done']:
                    completion_change = 'complete'
        else:
            fields.append('done=0')
            fields.append('completed_at=NULL')
            completion_change = 'uncomplete'
    if not fields:
        return jsonify({'error': 'nothing to update'}), 400

    fields.append('updated_at=?')
    values.extend([int(time.time()), todo_id])
    db.execute(f'UPDATE todos SET {", ".join(fields)} WHERE id=?', values)
    if completion_change == 'complete':
        _log_event(
            db, 'todo_completed', row['title'], todo_id, row['list'], row['notes']
        )
    elif completion_change == 'uncomplete':
        db.execute(
            "DELETE FROM task_events WHERE kind='todo_completed' AND ref_id=?",
            (todo_id,),
        )
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
