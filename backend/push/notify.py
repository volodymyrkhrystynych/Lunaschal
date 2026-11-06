"""Telling the phone that something needs it: device rows and the Code tab's
notifications.

`task_changed` is registered as a `backend.codeagent.tasks` listener, so it
fires on every transition into a state a person is waiting to hear about. It
sends on a short-lived thread: a slow or unreachable APNs must never stall the
worker that changed the task's state.
"""
from __future__ import annotations

import logging
import threading
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.push import apns

logger = logging.getLogger(__name__)

_MESSAGES = {
    'awaiting_plan': ('Plan ready', 'The agent has a plan for “{title}”. Approve it to start.'),
    'needs_input': ('Agent has a question', '“{title}”: {question}'),
    'done': ('Task finished', '“{title}” is ready to review{tests}.'),
    'error': ('Task failed', '“{title}” stopped: {error}'),
    'interrupted': ('Task interrupted', '“{title}” was interrupted. Continue it from the Code tab.'),
    'stuck': ('Agent is stuck', '“{title}”: the model keeps putting its tool calls in its thinking.'),
}


def register_device(token: str, environment: str = 'production', platform: str = 'ios') -> None:
    token = (token or '').strip()
    if not token or len(token) > 200 or not all(c in '0123456789abcdefABCDEF' for c in token):
        raise ValueError('That is not an APNs device token')
    if environment not in ('production', 'sandbox'):
        raise ValueError('environment must be production or sandbox')
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO push_devices (id, token, environment, platform, created_at, last_seen_at) '
        'VALUES (?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET '
        'environment=excluded.environment, last_seen_at=excluded.last_seen_at',
        (str(ULID()), token.lower(), environment, platform, now, now),
    )
    db.commit()


def devices() -> list[dict]:
    return [dict(r) for r in get_db().execute(
        'SELECT token, environment FROM push_devices ORDER BY last_seen_at DESC').fetchall()]


def message_for(task: dict, status: str) -> tuple[str, str] | None:
    template = _MESSAGES.get(status)
    if not template:
        return None
    pending = (task.get('pending') or {}).get('request') or {}
    tests = task.get('testExit')
    title, body = template
    return title, body.format(
        title=task.get('title') or 'Task',
        question=pending.get('question') or 'see the Code tab',
        tests='' if tests is None else (' — tests passed' if tests == 0 else ' — tests failed'),
        error=(task.get('error') or 'unknown error')[:120],
    )


def send_all(title: str, body: str, data: dict, *, collapse_id: str = '', client=None) -> int:
    """Send to every registered device. Returns how many accepted it; prunes
    tokens Apple says are gone."""
    config = apns.load_config()
    if config is None:
        return 0
    sent = 0
    message = apns.payload(title, body, data, thread=data.get('taskId', ''))
    for d in devices():
        result = apns.send(config, d['token'], message, environment=d['environment'],
                           collapse_id=collapse_id, client=client)
        if result.ok:
            sent += 1
        elif result.token_is_dead:
            get_db().execute('DELETE FROM push_devices WHERE token=?', (d['token'],))
            get_db().commit()
        else:
            logger.warning('APNs refused a push (%s %s)', result.status, result.reason)
    return sent


def task_changed(task: dict, status: str) -> None:
    msg = message_for(task, status)
    if msg is None or not apns.configured():
        return
    if 'pending' not in task:
        from backend.codeagent import tasks
        task = {**task, 'pending': tasks.pending_approval(task['id'])}
    data = {'kind': 'code_task', 'taskId': task['id'], 'status': status}
    threading.Thread(target=send_all, args=msg, kwargs={
        'data': data, 'collapse_id': f"code-{task['id']}"}, daemon=True,
        name='apns-send').start()
