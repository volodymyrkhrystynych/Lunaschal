"""Telling the phone that something needs it: device rows, the one loop over
them, and the Code tab's notifications.

The Code tab asks for notifications when its page is opened; the coach's
reminders have their own switch and stay off until it is turned on. So a row
says which it has agreed to: `coach` is set only by the phone's reminders
switch, a Code registration leaves it alone, and coach pushes go only to the
rows that have it. Turning reminders off clears the flag rather than deleting
the device, which would take the Code tab's notifications with it.

`task_changed` is registered as a `backend.codeagent.tasks` listener, so it
fires on every transition into a state a person is waiting to hear about. It
sends on a short-lived thread: a slow or unreachable APNs must never stall the
worker that changed the task's state.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.push import apns
from backend.push.payload import alert

logger = logging.getLogger(__name__)

TOKEN = re.compile(r'[0-9a-fA-F]{32,200}')

_MESSAGES = {
    'awaiting_plan': ('Plan ready', 'The agent has a plan for “{title}”. Approve it to start.'),
    'needs_input': ('Agent has a question', '“{title}”: {question}'),
    'done': ('Task finished', '“{title}” is ready to review{tests}.'),
    'error': ('Task failed', '“{title}” stopped: {error}'),
    'interrupted': ('Task interrupted', '“{title}” was interrupted. Continue it from the Code tab.'),
    'stuck': ('Agent is stuck', '“{title}”: the model keeps putting its tool calls in its thinking.'),
}


def register_device(token: str, environment: str = 'production', platform: str = 'ios', *,
                    bundle_id: str | None = None, app_version: str | None = None,
                    coach: bool | None = None) -> None:
    """Upsert a phone. Registering again re-enables a row Apple called dead;
    `coach=None` means this caller has no opinion about reminders."""
    token = (token or '').strip()
    if not TOKEN.fullmatch(token):
        raise ValueError('That is not an APNs device token')
    if environment not in apns.HOSTS:
        raise ValueError('environment must be production or sandbox')
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO push_devices (id, token, environment, platform, bundle_id, app_version, coach, '
        'created_at, last_seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET '
        'environment=excluded.environment, platform=excluded.platform, '
        'bundle_id=COALESCE(excluded.bundle_id, bundle_id), '
        'app_version=COALESCE(excluded.app_version, app_version), coach=COALESCE(?, coach), '
        'enabled=1, last_error=NULL, last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at',
        (str(ULID()), token.lower(), environment, platform, bundle_id, app_version, int(bool(coach)),
         now, now, now, None if coach is None else int(coach)),
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


def send(message: dict, *, collapse_id: str | None = None, coach_only: bool = False,
         now: int | None = None, client=None) -> dict:
    """Send one payload to every enabled device. A token Apple says is dead
    disables its row, and the error stays on it for Settings to show. Returns
    counts and errors; never raises for a refused push."""
    now = int(now if now is not None else time.time())
    client = client if client is not None else apns.client()
    if client is None:
        return {'pushed': 0, 'errors': [], 'configured': False}
    db = get_db()
    pushed, errors = 0, []
    query = 'SELECT * FROM push_devices WHERE enabled=1' + (' AND coach=1' if coach_only else '')
    for device in db.execute(query).fetchall():
        result = client.send(device['token'], device['environment'], message, collapse_id=collapse_id)
        if result.ok:
            pushed += 1
            db.execute('UPDATE push_devices SET last_sent_at=?, last_error=NULL, updated_at=? WHERE id=?',
                       (now, now, device['id']))
        else:
            reason = f'{result.status} {result.reason or ""}'.strip()
            errors.append(reason)
            logger.warning('Push to device %s failed: %s', device['id'], reason)
            db.execute('UPDATE push_devices SET enabled=?, last_error=?, updated_at=? WHERE id=?',
                       (0 if result.dead else 1, reason[:200], now, device['id']))
    db.commit()
    return {'pushed': pushed, 'errors': errors, 'configured': True}


def send_all(title: str, body: str, data: dict, *, collapse_id: str = '', client=None) -> int:
    """A Code-tab notification to every device. Returns how many accepted it."""
    message = alert(title, body, root=data, thread_id=data.get('taskId', ''))
    return send(message, collapse_id=collapse_id or None, client=client)['pushed']


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
