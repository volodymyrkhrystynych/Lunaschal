import logging
import re
import time
from datetime import date, datetime, timezone

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend import todo_storage
from backend.ai import jobs
from backend.ai.service import InferencePaused, Preempted
from backend.capture_time import optional_capture_time
from backend import day_plan
from backend.db.connection import get_db, mapping_to_dict, row_to_dict
from backend.day_boundary import day_bounds, day_key_for
from backend.ai.todo import TITLE_LIMIT, extract_todo_fields
from backend.habit_streaks import summary as streak_summary
from backend.todo_recurrence import (
    next_due, normalize_list, parse_due_date,
    parse_priority as _parse_priority,
    parse_repeat as _parse_repeat,
)

bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')
logger = logging.getLogger(__name__)

# Daily tasks are the habits. Still capped: a list worth ticking every day
# stays short, and the phone's largest Habits widget has room for this many.
MAX_TASKS = 10


def _today() -> str:
    return day_key_for()


_DAY_KEY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _requested_day() -> tuple[str | None, str | None]:
    """The day a completion is for: `?date=` when given, else today.

    A client that ticked a task off while offline sends the day it was ticked
    on, so a tick replayed after the 4am rollover still lands on its own day
    instead of on the next one. Returns (day_key, error_or_None)."""
    value = request.args.get('date')
    if value is None:
        return _today(), None
    if not _DAY_KEY.match(value):
        return None, 'date must be YYYY-MM-DD'
    try:
        date.fromisoformat(value)
    except ValueError:
        return None, 'date must be a real date'
    return value, None


def _log_event(
    db,
    kind: str,
    title: str,
    ref_id: str | None = None,
    task_list: str | None = None,
    detail: str | None = None,
    created_at: int | None = None,
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
        (str(ULID()), kind, title, ref_id, task_list, detail or None,
         created_at if created_at is not None else int(time.time())),
    )


def _acted_at(clock: int) -> int:
    """When the user did this: the phone's `capturedAt`, else now.

    The phone queues these offline and may send them hours later — from a
    background sync in the small hours, say — and task_events is one of the
    things backend/sleep.py reads as the user being awake. A query parameter
    rather than a body field, because deletes carry no body. Never later than
    now: a capture time from the future is a wrong clock. Raises ValueError for
    a malformed value, as the journal and food routes refuse one.
    """
    captured = optional_capture_time(request.args)
    return min(captured, clock) if captured is not None else clock


@bp.get('')
def list_tasks():
    today = _today()
    db = get_db()
    rows = db.execute(
        '''
        SELECT t.id, t.title, t.position, t.cue, t.created_at, t.updated_at,
               CASE WHEN c.id IS NOT NULL THEN 1 ELSE 0 END AS done
        FROM daily_tasks t
        LEFT JOIN daily_task_completions c ON c.task_id = t.id AND c.date = ?
        ORDER BY t.position
        ''',
        (today,),
    ).fetchall()
    # Every completion, not just the window: the longest streak needs them all,
    # and there are at most ten tasks with one row per day each.
    done: dict[str, list[str]] = {}
    for c in db.execute('SELECT task_id, date FROM daily_task_completions'):
        done.setdefault(c['task_id'], []).append(c['date'])
    excused = excused_habit_days(db)
    return jsonify([
        {**row_to_dict(r), **mapping_to_dict(streak_summary(
            done.get(r['id'], []), today, excused.get(r['id'], set()) | excused.get(None, set())))}
        for r in rows
    ])


def excused_habit_days(db) -> dict[str | None, set[str]]:
    """Days each habit was excused on, keyed by task id; `None` holds the
    days excused whole, which count for every habit. Only an external or
    one-off reason excuses — an internal one is an obstacle, not a pass."""
    days: dict[str | None, set[str]] = {}
    for r in db.execute(
        "SELECT day, target_kind, target_id FROM day_excuses "
        "WHERE kind IN ('external','one_off') AND target_kind IN ('habit','day')"
    ):
        key = r['target_id'] if r['target_kind'] == 'habit' else None
        days.setdefault(key, set()).add(r['day'])
    return days


@bp.post('')
def create_task():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400

    # Accept a client-supplied ULID so an offline-queued create replays
    # idempotently: a repeat answers as the first one did, before the cap is
    # checked, since the first one may be what filled it.
    task_id = body.get('id') or str(ULID())
    if not isinstance(task_id, str) or not re.fullmatch(r'[0-9A-Za-z]{1,64}', task_id):
        return jsonify({'error': 'id must be a ULID'}), 400
    try:
        insert_daily_task(get_db(), title, task_id=task_id)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'id': task_id}), 201


def insert_daily_task(db, title: str, *, task_id: str | None = None, cue: str | None = None) -> str:
    """Add a habit at the end of the list; the one place the cap is kept.
    An existing id is a replay and is answered as the first create was."""
    task_id = task_id or str(ULID())
    if db.execute('SELECT 1 FROM daily_tasks WHERE id=?', (task_id,)).fetchone():
        return task_id
    count = db.execute('SELECT COUNT(*) FROM daily_tasks').fetchone()[0]
    if count >= MAX_TASKS:
        raise ValueError(f'max {MAX_TASKS} tasks allowed')
    now = int(time.time())
    db.execute(
        'INSERT INTO daily_tasks(id, title, position, cue, created_at, updated_at) VALUES (?,?,?,?,?,?)',
        (task_id, title, count + 1, (cue or '').strip()[:200] or None, now, now),
    )
    db.commit()
    return task_id


@bp.patch('/<task_id>')
def update_task(task_id):
    """Rename a habit and/or set its cue — the "after coffee" of "weigh in
    after coffee". Either field may come alone; an empty cue clears it."""
    body = request.json or {}
    if 'title' not in body and 'cue' not in body:
        return jsonify({'error': 'title required'}), 400
    db = get_db()
    now = int(time.time())
    if 'title' in body:
        title = (body.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'title required'}), 400
        db.execute('UPDATE daily_tasks SET title=?, updated_at=? WHERE id=?', (title, now, task_id))
    if 'cue' in body:
        cue = body.get('cue')
        if cue is not None and not isinstance(cue, str):
            return jsonify({'error': 'cue must be text'}), 400
        cue = (cue or '').strip()[:200] or None
        db.execute('UPDATE daily_tasks SET cue=?, updated_at=? WHERE id=?', (cue, now, task_id))
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

    try:
        acted = _acted_at(int(time.time()))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    deleted_pos = row['position']
    _log_event(db, 'task_deleted', row['title'], task_id, 'daily', created_at=acted)
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
    owns the transaction. Shared with the chat's day-plan bar, which ticks a
    linked habit through here — and is ticked back from here."""
    cur = db.execute(
        'INSERT OR IGNORE INTO daily_task_completions(id, task_id, date, created_at)'
        ' VALUES (?,?,?,?)',
        (str(ULID()), task_id, today, now),
    )
    day_plan.mark_bar_for_source(db, 'daily', task_id, today, True, now)
    # Only log when this call actually recorded a completion (not a repeat POST).
    if not cur.rowcount:
        return False
    task = db.execute(
        'SELECT title FROM daily_tasks WHERE id=?', (task_id,)
    ).fetchone()
    if task:
        _log_event(db, 'daily_completed', task['title'], task_id, 'daily',
                   created_at=now)
    return True


@bp.post('/<task_id>/complete')
def complete_task(task_id):
    day, err = _requested_day()
    if err:
        return jsonify({'error': err}), 400
    try:
        acted = _acted_at(int(time.time()))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = get_db()
    # Stamped when it was ticked, if the phone says. Without that, a tick
    # replayed from a past day is filed in that day's last second, so its
    # Journal notification still lands on the day it was made.
    now = min(acted, day_bounds(day)[1] - 1)
    complete_daily_task(db, task_id, day, now)
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<task_id>/complete')
def uncomplete_task(task_id):
    day, err = _requested_day()
    if err:
        return jsonify({'error': err}), 400
    db = get_db()
    uncomplete_daily_task(db, task_id, day, int(time.time()))
    db.commit()
    return jsonify({'success': True})


def uncomplete_daily_task(db, task_id: str, day: str, now: int) -> None:
    """Undo `day`'s completion of a habit. Does not commit."""
    db.execute(
        'DELETE FROM daily_task_completions WHERE task_id=? AND date=?',
        (task_id, day),
    )
    # Retract that day's completion notification so toggling off leaves no
    # phantom, and only that day's: complete_task files a replayed tick inside
    # its own day, so another day's notification is never in this window.
    start, end = day_bounds(day)
    db.execute(
        "DELETE FROM task_events WHERE kind='daily_completed' AND ref_id=?"
        " AND created_at >= ? AND created_at < ?",
        (task_id, start, end),
    )
    day_plan.mark_bar_for_source(db, 'daily', task_id, day, False, now)


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
    caller owns the transaction. Shared with the chat's day-plan bar, which
    must complete a linked todo exactly the way the Tasks view does — and is
    ticked back from here."""
    row = db.execute(
        'SELECT title, done, list, notes, due, repeat_interval, repeat_unit'
        ' FROM todos WHERE id=?',
        (todo_id,),
    ).fetchone()
    if row is None:
        return False
    # A repeating to-do is reopened for its next occurrence below, but today's
    # bar row for it is still done.
    day = day_key_for(now)
    day_plan.mark_bar_for_source(db, 'todo', todo_id, day, True, now)

    if row['repeat_interval'] and row['repeat_unit']:
        # Repeating todo: completing it schedules the next occurrence instead of
        # marking it done. Each occurrence is a real completion, so always log it.
        day_plan.record_occurrence_due(db, todo_id, day, row['due'])
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

    _log_event(db, 'todo_completed', row['title'], todo_id, row['list'], row['notes'], created_at=now)
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
    day_plan.mark_bar_for_source(db, 'todo', todo_id, day_key_for(now), False, now)


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
    try:
        acted = _acted_at(now)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if fields:
        fields.append('updated_at=?')
        values.extend([now, todo_id])
        db.execute(f'UPDATE todos SET {", ".join(fields)} WHERE id=?', values)
    if completion_change == 'complete':
        complete_todo_row(db, todo_id, acted)
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
    try:
        acted = _acted_at(int(time.time()))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if row and not row['done']:
        _log_event(
            db, 'task_deleted', row['title'], todo_id, row['list'], row['notes'],
            created_at=acted,
        )
    db.execute('DELETE FROM todos WHERE id=?', (todo_id,))
    db.commit()
    return jsonify({'success': True})



# --- Chat to-dos (ephemeral, day-scoped items in the Chat tab's bar) ---

_CHAT_TODO_COLS = (
    'id, day_key, title, notes, due, priority, done, completed_at, created_at, updated_at,'
    ' source_type, source_id, reason'
)

MAX_CHAT_TODO_ITEMS = 10


def _today_chat_todo_titles(db, today: str) -> set[str]:
    """Normalized titles already sitting in today's bar. Every writer checks
    this so a forced re-run or a same-batch repeat can't insert a title twice.
    Restating an open to-do or a pending habit is *not* a duplicate any more:
    the row is linked to it (backend/day_plan.py), and ticking either ticks
    both."""
    return {
        day_plan.normalize_title(r['title'])
        for r in db.execute(
            'SELECT title FROM chat_todos WHERE day_key=?', (today,)
        ).fetchall()
    }


def _today_bar_sources(db, today: str) -> set[tuple[str, str]]:
    """(type, id) of every list item already in today's bar under any title,
    so "call the dentist" can't go in twice as two wordings of one to-do."""
    return {
        (r['source_type'], r['source_id'])
        for r in db.execute(
            'SELECT source_type, source_id FROM chat_todos WHERE day_key=? AND source_id IS NOT NULL',
            (today,),
        )
    }


def add_to_bar(db, today: str, items: list, now: int) -> list[dict]:
    """Insert live-chat items into today's bar, linked to the open to-do or
    habit each restates. Skips what is already in the bar (by title or by the
    row it links to). Returns the created rows. Does not commit."""
    taken = _today_chat_todo_titles(db, today)
    linked = _today_bar_sources(db, today)
    sources = day_plan.list_sources(db, today)
    created = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or '').strip()
        if not title or day_plan.normalize_title(title) in taken:
            continue
        source = day_plan.resolve_list_source(db, today, title, sources=sources)
        if source and source in linked:
            continue
        notes = (item.get('notes') or '').strip() or None
        row_id = day_plan.insert_bar_row(db, today, title, now, notes=notes, source=source)
        taken.add(day_plan.normalize_title(title))
        if source:
            linked.add(source)
        row = mapping_to_dict({
            'id': row_id, 'day_key': today, 'title': title, 'notes': notes,
            'due': None, 'priority': 3, 'done': 0, 'completed_at': None,
            'created_at': now, 'updated_at': now,
            'source_type': source[0] if source else None,
            'source_id': source[1] if source else None, 'reason': None,
        })
        row['done'] = False
        created.append(row)
    return created


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
    created = add_to_bar(db, _today(), items[:MAX_CHAT_TODO_ITEMS], int(time.time()))
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
        'SELECT title, done, notes, day_key, source_type, source_id FROM chat_todos WHERE id=?',
        (chat_todo_id,),
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
    linked = (row['source_type'] in day_plan.LIST_SOURCES
              and day_plan.source_exists(db, row['source_type'], row['source_id']))
    if linked and completion_change and bool(row['done']) == (completion_change == 'complete'):
        # Already in that state: a double tap or a replayed request. Going
        # through the source again would advance a repeating to-do a second
        # time and log a second completion.
        pass
    elif linked and completion_change:
        # Through the real row, which logs the one notification and sets this
        # bar row (and any other linked to it) on its way.
        if completion_change == 'complete':
            day_plan.complete_source(db, row['source_type'], row['source_id'], row['day_key'], now)
        else:
            day_plan.uncomplete_source(db, row['source_type'], row['source_id'], row['day_key'], now,
                                       bar_row_id=chat_todo_id)
        # The source may already have been in that state (done in Tasks before
        # the link existed), so the bar row is set directly as well.
        db.execute(
            'UPDATE chat_todos SET done=?, completed_at=?, updated_at=? WHERE id=?',
            (1 if completion_change == 'complete' else 0,
             now if completion_change == 'complete' else None, now, chat_todo_id),
        )
    elif completion_change == 'complete':
        _complete_chat_todo_row(db, chat_todo_id, now)
        day_plan.reset_streak(db, day_plan.suggestion_key(
            row['source_type'], row['source_id'], row['title']), now)
    elif completion_change == 'uncomplete':
        _uncomplete_chat_todo_row(db, chat_todo_id, now)
    db.commit()
    return jsonify({'success': True})


@bp.delete('/chat-todos/<chat_todo_id>')
def delete_chat_todo(chat_todo_id):
    # No task_events log — dismissing an ephemeral item was never a notable
    # event. It is remembered as a suggestion that was turned down, though, so
    # the plan stops offering it for a few days (backend/day_plan.py).
    db = get_db()
    row = db.execute(
        'SELECT title, done, source_type, source_id FROM chat_todos WHERE id=?', (chat_todo_id,),
    ).fetchone()
    if row and not row['done']:
        day_plan.record_dismissal(db, row, _today(), int(time.time()))
    db.execute('DELETE FROM chat_todos WHERE id=?', (chat_todo_id,))
    db.commit()
    return jsonify({'success': True})


@bp.post('/chat-todos/<chat_todo_id>/promote')
def promote_chat_todo(chat_todo_id):
    """Move a chat to-do to the permanent list: insert via the same validation
    path create_todo uses, then remove the ephemeral row — one transaction, so
    a rejected payload leaves the source row untouched."""
    db = get_db()
    row = db.execute('SELECT source_type, source_id FROM chat_todos WHERE id=?',
                     (chat_todo_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    # Already a real to-do or habit: promoting it would make a second copy.
    if row['source_type'] in day_plan.LIST_SOURCES and day_plan.source_exists(
            db, row['source_type'], row['source_id']):
        return jsonify({'error': 'already on your list'}), 409
    todo_id, err = _insert_todo_row(db, request.json or {})
    if err:
        return jsonify({'error': err}), 400
    db.execute('DELETE FROM chat_todos WHERE id=?', (chat_todo_id,))
    db.commit()
    return jsonify({'id': todo_id}), 201


@bp.post('/todos/transcribe')
def transcribe_todo():
    """Turn a spoken sentence into a to-do, with no confirmation step.

    The client sends already-transcribed text (via ``/api/transcribe``) and,
    optionally, an ``id`` it minted. The model extracts title, notes, priority
    and due date, resolving relative dates against today's 4am day. If it is
    unavailable (a paused GPU is normal), the transcript itself becomes the
    to-do: the first line's worth as the title and, when that had to be cut,
    the whole of it as the notes, so nothing that was said is lost.

    A replayed ``id`` returns the existing row without asking the model again,
    which is what lets the phone retry, or fall back to a plain create with the
    same id, without making two to-dos.
    """
    body = request.json or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    client_id = body.get('id')
    if client_id is not None and (not isinstance(client_id, str) or not client_id.strip()):
        return jsonify({'error': 'id must be a non-empty string'}), 400

    db = get_db()
    if client_id and db.execute('SELECT 1 FROM todos WHERE id=?', (client_id,)).fetchone():
        return jsonify({'id': client_id}), 200

    todo_id, err = _todo_from_speech(db, text, client_id)
    if err:
        return jsonify({'error': err}), 400
    db.commit()
    return jsonify({'id': todo_id}), 201


def _todo_from_speech(db, text: str, todo_id: str | None) -> tuple[str | None, str | None]:
    """Insert the to-do a spoken sentence describes; the caller commits.

    Shared by the synchronous route above and the recording job below, so a
    to-do said into the phone comes out the same whichever way it arrived.
    """
    fields = extract_todo_fields(text, day_key_for())
    if fields is None:
        title = text[:TITLE_LIMIT].strip()
        fields = {
            'title': title,
            'notes': text if title != text else None,
            'priority': None,
            'due': None,
        }

    due_ts = None
    if fields.get('due'):
        due_ts, err = parse_due_date(fields['due'])
        if err:
            due_ts = None  # an unparseable date from the model is dropped, not fatal

    return _insert_todo_row(db, {
        'id': todo_id,
        'title': fields['title'],
        'notes': fields.get('notes'),
        'priority': fields.get('priority'),
        'due': due_ts,
    })


# --- Spoken to-dos ------------------------------------------------------------
#
# The phone's mic beside "Add to-do". It used to upload the clip to
# /api/transcribe, wait for the words, then wait again on /todos/transcribe, and
# delete the clip whatever happened — so a recording made offline, or one the
# server failed on, was simply gone. This is the journal/food/chat recording
# contract instead: the phone keeps the clip until this route has it, re-POSTs
# on every reconnect, and the id it minted is both the recording's and the
# to-do's, so a replay is a no-op checked before the file is read. Stopping the
# recording is the last thing the user waits for.

MAX_AUDIO_BYTES = 100 * 1024 * 1024


@bp.post('/todos/recordings')
def create_todo_recording():
    audio = request.files.get('audio')
    if audio is None or not audio.filename:
        return jsonify({'error': 'Missing audio file'}), 400
    recording_id = (request.form.get('id') or '').strip()
    if not recording_id:
        return jsonify({'error': 'id is required'}), 400

    db = get_db()
    # Before the file is read: a retry must not stream the clip to disk again
    # only to find it already here, or already a to-do.
    existing = db.execute('SELECT * FROM todo_recordings WHERE id=?', (recording_id,)).fetchone()
    if existing is not None:
        return jsonify(_recording_dict(existing)), 200
    if db.execute('SELECT 1 FROM todos WHERE id=?', (recording_id,)).fetchone():
        return jsonify({'id': recording_id, 'status': 'done'}), 200

    ext = todo_storage.audio_ext(audio.mimetype, audio.filename)
    path = todo_storage.clip_path(recording_id, ext) if ext else None
    if path is None:
        return jsonify({'error': 'Unsupported file type — audio only'}), 400
    try:
        captured_at = optional_capture_time(request.form)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    path.parent.mkdir(parents=True, exist_ok=True)
    # Streamed to disk, not read() into memory: this also runs on 8 GB machines.
    audio.save(path)
    size = path.stat().st_size
    if size == 0 or size > MAX_AUDIO_BYTES:
        todo_storage.delete_recording_dir(recording_id)
        return jsonify({
            'error': 'That recording was empty' if size == 0 else 'That recording is too large'
        }), 400 if size == 0 else 413

    clock = int(time.time())
    db.execute(
        'INSERT INTO todo_recordings(id, path, mime, status, captured_at, created_at)'
        " VALUES (?,?,?,'pending',?,?)",
        (recording_id, str(path), audio.mimetype, min(captured_at, clock) if captured_at else None, clock),
    )
    jobs.enqueue('todo.transcribe_recording', recording_id, commit=False)
    db.commit()
    return jsonify(_recording_dict(_load_recording(db, recording_id))), 201


@bp.get('/todos/recordings')
def list_todo_recordings():
    """Spoken to-dos that are not on the list yet: still being transcribed, or
    failed. A finished one is just a to-do, and the list already shows it."""
    rows = get_db().execute(
        "SELECT * FROM todo_recordings WHERE status IN ('pending','error') ORDER BY created_at"
    ).fetchall()
    return jsonify([_recording_dict(r) for r in rows])


@bp.delete('/todos/recordings/<recording_id>')
def delete_todo_recording(recording_id):
    """Dismiss a failed one, clip and all. A pending one is refused: its job is
    about to make the to-do, and deleting under it would race that."""
    db = get_db()
    row = _load_recording(db, recording_id)
    if row is None:
        return jsonify({'error': 'not found'}), 404
    if row['status'] == 'pending':
        return jsonify({'error': 'Still being transcribed'}), 409
    db.execute('DELETE FROM todo_recordings WHERE id=?', (recording_id,))
    db.commit()
    todo_storage.delete_recording_dir(recording_id)
    return jsonify({'success': True})


def _load_recording(db, recording_id: str):
    return db.execute('SELECT * FROM todo_recordings WHERE id=?', (recording_id,)).fetchone()


def _recording_dict(row) -> dict:
    d = row_to_dict(row)
    d.pop('path', None)  # a server path is nothing a client should hold
    d.pop('mime', None)
    d['capturedAt'] = (
        datetime.fromtimestamp(row['captured_at'], tz=timezone.utc).isoformat()
        if row['captured_at'] else None
    )
    return d


def _finish_recording(db, recording_id: str, status: str, error: str | None = None) -> None:
    if status == 'done':
        db.execute("UPDATE todo_recordings SET status='done', error=NULL, path=NULL WHERE id=?",
                   (recording_id,))
        db.commit()
        todo_storage.delete_recording_dir(recording_id)
    else:
        db.execute('UPDATE todo_recordings SET status=?, error=? WHERE id=?',
                   (status, error, recording_id))
        db.commit()


def _transcribe_recording_bg(recording_id: str, *, now: bool = False) -> None:
    """Transcribe a spoken to-do and put it on the list.

    The transcript is saved before the model is asked to sort it into fields,
    so a run that is cancelled afterwards (`Preempted`, re-raised for the job
    row to bring back) does not transcribe the clip twice. A paused GPU is not
    waited out: `extract_todo_fields` comes back empty and the words become the
    to-do as they were said, exactly as the synchronous route does — a pause
    lasts hours, and the to-do is wanted now.
    """
    if not now:
        jobs.enqueue('todo.transcribe_recording', recording_id)
        return
    from backend.routes import stt as stt_routes

    db = get_db()
    row = _load_recording(db, recording_id)
    if row is None or row['status'] != 'pending':
        return
    if db.execute('SELECT 1 FROM todos WHERE id=?', (recording_id,)).fetchone():
        _finish_recording(db, recording_id, 'done')  # made on an earlier run
        return

    text = (row['transcript'] or '').strip()
    if not text:
        path = todo_storage.resolve_stored_path(row['path']) if row['path'] else None
        try:
            if path is None or not path.is_file():
                raise RuntimeError('The recording is missing')
            text = (stt_routes.transcribe_file(path) or '').strip()
        except (InferencePaused, Preempted):
            raise
        except Exception as e:
            logger.warning('Transcribing to-do recording %s failed: %s', recording_id, e)
            _finish_recording(db, recording_id, 'error', str(e) or 'Failed')
            return
        if not text:
            _finish_recording(db, recording_id, 'error', 'No speech was heard in that recording.')
            return
        db.execute('UPDATE todo_recordings SET transcript=? WHERE id=?', (text, recording_id))
        db.commit()

    _, err = _todo_from_speech(db, text, recording_id)
    if err:
        db.rollback()
        _finish_recording(db, recording_id, 'error', err)
        return
    db.commit()
    _finish_recording(db, recording_id, 'done')


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
