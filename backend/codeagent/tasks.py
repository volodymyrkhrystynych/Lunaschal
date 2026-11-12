"""The Code tab's rows: tasks, their ordered event log, and what waits on you.

Everything here is DB-only and synchronous, so the driver, the routes and the
tests share one definition of each state change. `set_status` is the one place
a task changes state, which is also where the push notifier hooks in — a
status the user needs to hear about cannot be reached without passing it.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from ulid import ULID

from backend.db.connection import get_db, row_to_dict

HARNESSES = ('pi', 'omp', 'qwen')
# What a new task is: a question to answer (phase 'ask') or a change, which
# starts by planning (phase 'plan').
MODES = ('ask', 'plan')

# States a person is waiting to hear about; set_status notifies on entry.
NOTIFY_STATES = frozenset({'answered', 'awaiting_plan', 'needs_input', 'done', 'error',
                           'interrupted'})
ACTIVE_STATES = frozenset({'queued', 'answering', 'planning', 'running', 'testing'})
# A task in one of these is finished with its container and workspace.
CLOSED_STATES = frozenset({'cancelled', 'pushed', 'discarded'})

_seq_lock = threading.Lock()
_listeners: list[Callable[[dict, str], None]] = []


class TaskNotFound(LookupError):
    pass


class BadTransition(ValueError):
    pass


def add_listener(fn: Callable[[dict, str], None]) -> None:
    """Called as fn(task, new_status) whenever a task enters a NOTIFY_STATE.
    A listener that raises is logged and ignored — a failed push must never
    fail the state change it was announcing."""
    if fn not in _listeners:
        _listeners.append(fn)


def _now() -> int:
    return int(time.time())


def _decode(task: dict) -> dict:
    raw = task.get('diffStat')
    task['diffStat'] = json.loads(raw) if raw else None
    return task


def create(repo_id: str, prompt: str, *, title: str = '', harness: str = 'pi',
           base_branch: str = '', mode: str = 'plan') -> dict:
    prompt = (prompt or '').strip()
    if not prompt:
        raise ValueError('A task needs a description')
    if harness not in HARNESSES:
        raise ValueError(f'Unknown agent {harness!r}')
    if mode not in MODES:
        raise ValueError(f'Unknown mode {mode!r}')
    task_id = str(ULID())
    now = _now()
    title = (title or '').strip() or title_from(prompt)
    db = get_db()
    db.execute(
        'INSERT INTO agent_tasks (id, repo_id, title, prompt, harness, status, phase, '
        "base_branch, created_at, updated_at) VALUES (?,?,?,?,?,'queued',?,?,?,?)",
        (task_id, repo_id, title, prompt, harness, mode, base_branch, now, now),
    )
    db.commit()
    append_event(task_id, 'user', {'text': prompt})
    return get(task_id)


def title_from(prompt: str) -> str:
    line = prompt.strip().splitlines()[0]
    return line if len(line) <= 80 else line[:77].rstrip() + '…'


def get(task_id: str) -> dict:
    row = get_db().execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        raise TaskNotFound(task_id)
    return _decode(row_to_dict(row))


def list_tasks(limit: int = 50) -> list[dict]:
    rows = get_db().execute(
        'SELECT * FROM agent_tasks ORDER BY updated_at DESC, id DESC LIMIT ?', (limit,)
    ).fetchall()
    return [_decode(row_to_dict(r)) for r in rows]


def next_queued() -> dict | None:
    row = get_db().execute(
        "SELECT * FROM agent_tasks WHERE status='queued' ORDER BY created_at, id LIMIT 1"
    ).fetchone()
    return _decode(row_to_dict(row)) if row else None


_COLUMNS = frozenset({
    'phase', 'session_id', 'pending_input', 'title', 'base_branch', 'base_sha', 'head_sha', 'plan', 'summary', 'diff_stat',
    'test_exit', 'test_output', 'pr_url', 'error', 'started_at', 'finished_at',
})


def update(task_id: str, **fields) -> None:
    bad = set(fields) - _COLUMNS
    if bad:
        raise ValueError(f'not updatable: {sorted(bad)}')
    if 'diff_stat' in fields and not isinstance(fields['diff_stat'], (str, type(None))):
        fields['diff_stat'] = json.dumps(fields['diff_stat'])
    fields['updated_at'] = _now()
    sets = ', '.join(f'{k}=?' for k in fields)
    db = get_db()
    cur = db.execute(f'UPDATE agent_tasks SET {sets} WHERE id=?', (*fields.values(), task_id))
    db.commit()
    if cur.rowcount == 0:
        raise TaskNotFound(task_id)


def set_status(task_id: str, status: str, **fields) -> dict:
    db = get_db()
    now = _now()
    extra = dict(fields)
    if status in ('answering', 'planning') and 'started_at' not in extra:
        row = db.execute('SELECT started_at FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
        if row and row['started_at'] is None:
            extra['started_at'] = now
    if status in ('done', 'error', 'cancelled', 'pushed', 'discarded') and 'finished_at' not in extra:
        extra['finished_at'] = now
    bad = set(extra) - _COLUMNS
    if bad:
        raise ValueError(f'not updatable: {sorted(bad)}')
    if 'diff_stat' in extra and not isinstance(extra['diff_stat'], (str, type(None))):
        extra['diff_stat'] = json.dumps(extra['diff_stat'])
    sets = ''.join(f', {k}=?' for k in extra)
    cur = db.execute(
        f'UPDATE agent_tasks SET status=?, updated_at=?{sets} WHERE id=?',
        (status, now, *extra.values(), task_id),
    )
    db.commit()
    if cur.rowcount == 0:
        raise TaskNotFound(task_id)
    append_event(task_id, 'status', {'status': status, **(
        {'error': extra['error']} if extra.get('error') else {})})
    task = get(task_id)
    if status in NOTIFY_STATES:
        for fn in list(_listeners):
            try:
                fn(task, status)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('task listener failed')
    return task


# ------------------------------------------------------------------- events

def append_event(task_id: str, kind: str, payload: dict | None = None) -> int:
    db = get_db()
    with _seq_lock:
        row = db.execute('SELECT COALESCE(MAX(seq), 0) FROM agent_task_events WHERE task_id=?',
                         (task_id,)).fetchone()
        seq = row[0] + 1
        db.execute(
            'INSERT INTO agent_task_events (id, task_id, seq, kind, payload, created_at) '
            'VALUES (?,?,?,?,?,?)',
            (str(ULID()), task_id, seq, kind, json.dumps(payload or {}), _now()),
        )
        db.commit()
    return seq


def events(task_id: str, after: int = 0, limit: int = 500) -> list[dict]:
    rows = get_db().execute(
        'SELECT seq, kind, payload, created_at FROM agent_task_events '
        'WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?', (task_id, after, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d['payload'] = json.loads(d['payload'] or '{}')
        out.append(d)
    return out


# What `worker.plan_from_answer` logs when the user typed nothing with it.
PLAN_THIS = 'Plan this.'


def question_thread(task_id: str) -> dict | None:
    """What a question task said before it became a change: the question and
    each follow-up, in order, and the change the user then asked for (None when
    they only said "Plan this"). None for a task that started as a change.

    Read from the event log rather than the row because the row keeps neither:
    `summary` holds only the latest answer, and `pending_input` is cleared the
    moment a run picks it up — so a run that dies takes the typed request with
    it, and a fresh session would otherwise be told only the first question."""
    rows = get_db().execute(
        "SELECT payload FROM agent_task_events WHERE task_id=? AND kind='user' ORDER BY seq",
        (task_id,),
    ).fetchall()
    questions: list[str] = []
    for r in rows:
        payload = json.loads(r['payload'] or '{}')
        action = payload.get('action')
        if action == 'plan':
            request = payload.get('request')
            if request is None:  # logged before 'request' was
                request = payload.get('text') if payload.get('text') != PLAN_THIS else None
            return {'questions': questions, 'planned': True, 'request': request or None}
        if action in (None, 'message') and payload.get('text'):
            questions.append(payload['text'])
    task = get_db().execute('SELECT phase FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
    if not task or task['phase'] != 'ask':
        return None
    return {'questions': questions, 'planned': False, 'request': None}


def last_event(task_id: str, kind: str) -> dict | None:
    """The newest event of one kind — however long the log has grown."""
    r = get_db().execute(
        'SELECT seq, kind, payload, created_at FROM agent_task_events '
        'WHERE task_id=? AND kind=? ORDER BY seq DESC LIMIT 1', (task_id, kind),
    ).fetchone()
    if r is None:
        return None
    d = row_to_dict(r)
    d['payload'] = json.loads(d['payload'] or '{}')
    return d


# --------------------------------------------------------------- approvals

def open_approval(task_id: str, kind: str, request: dict) -> str:
    """Expire anything still pending for the task, then open a new one: a task
    waits on at most one thing at a time."""
    db = get_db()
    now = _now()
    db.execute("UPDATE agent_approvals SET status='expired', answered_at=? "
               "WHERE task_id=? AND status='pending'", (now, task_id))
    approval_id = str(ULID())
    db.execute(
        'INSERT INTO agent_approvals (id, task_id, kind, request, created_at) VALUES (?,?,?,?,?)',
        (approval_id, task_id, kind, json.dumps(request), now),
    )
    db.commit()
    return approval_id


def pending_approval(task_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM agent_approvals WHERE task_id=? AND status='pending' "
        'ORDER BY created_at DESC LIMIT 1', (task_id,),
    ).fetchone()
    if not row:
        return None
    d = row_to_dict(row)
    d['request'] = json.loads(d['request'] or '{}')
    return d


def answer_approval(approval_id: str, status: str, response: str = '') -> None:
    if status not in ('approved', 'rejected', 'answered'):
        raise ValueError(status)
    db = get_db()
    cur = db.execute(
        "UPDATE agent_approvals SET status=?, response=?, answered_at=? "
        "WHERE id=? AND status='pending'", (status, response, _now(), approval_id),
    )
    db.commit()
    if cur.rowcount == 0:
        raise BadTransition('That request has already been answered')


def waiting_count() -> int:
    return get_db().execute(
        "SELECT COUNT(*) FROM agent_approvals WHERE status='pending'").fetchone()[0]
