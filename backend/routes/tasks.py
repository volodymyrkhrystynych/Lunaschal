import time
from datetime import date

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, row_to_dict

bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

MAX_TASKS = 4


def _today() -> str:
    return date.today().isoformat()


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
    row = db.execute('SELECT position FROM daily_tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    deleted_pos = row['position']
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
    try:
        db.execute(
            'INSERT INTO daily_task_completions(id, task_id, date, created_at) VALUES (?,?,?,?)',
            (str(ULID()), task_id, today, int(time.time())),
        )
        db.commit()
    except Exception:
        pass  # UNIQUE constraint: already done today
    return jsonify({'success': True})


@bp.delete('/<task_id>/complete')
def uncomplete_task(task_id):
    today = _today()
    db = get_db()
    db.execute(
        'DELETE FROM daily_task_completions WHERE task_id=? AND date=?',
        (task_id, today),
    )
    db.commit()
    return jsonify({'success': True})


# --- Todos (one-off items, unlike daily tasks they don't reset each day) ---


@bp.get('/todos')
def list_todos():
    db = get_db()
    rows = db.execute(
        'SELECT id, title, done, created_at, updated_at FROM todos ORDER BY done, created_at'
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/todos')
def create_todo():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400

    now = int(time.time())
    todo_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO todos(id, title, done, created_at, updated_at) VALUES (?,?,0,?,?)',
        (todo_id, title, now, now),
    )
    db.commit()
    return jsonify({'id': todo_id}), 201


@bp.patch('/todos/<todo_id>')
def update_todo(todo_id):
    body = request.json or {}
    fields = []
    values = []
    if 'title' in body:
        title = (body.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'title required'}), 400
        fields.append('title=?')
        values.append(title)
    if 'done' in body:
        fields.append('done=?')
        values.append(1 if body['done'] else 0)
    if not fields:
        return jsonify({'error': 'nothing to update'}), 400

    fields.append('updated_at=?')
    values.extend([int(time.time()), todo_id])
    db = get_db()
    db.execute(f'UPDATE todos SET {", ".join(fields)} WHERE id=?', values)
    db.commit()
    return jsonify({'success': True})


@bp.delete('/todos/<todo_id>')
def delete_todo(todo_id):
    db = get_db()
    db.execute('DELETE FROM todos WHERE id=?', (todo_id,))
    db.commit()
    return jsonify({'success': True})
