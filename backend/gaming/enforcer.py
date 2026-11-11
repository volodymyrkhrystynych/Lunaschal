"""The loop that keeps games closed while the day is locked.

Every few seconds: if today is locked, close whatever matches the patterns.
The first time it closes something on a day it says why — a chat message and
a push, through the coach's `deliver` — so a launcher vanishing is never a
mystery, and the reply to that message is exactly where "I'm done for the
day" goes. Once a day only; a launcher retried ten times is not ten messages.

No model call anywhere: this has to work with the GPU paused, which is the
state a gaming evening is spent in.
"""
import json
import logging
import os
import threading
import time

from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.gaming import lock, processes

logger = logging.getLogger(__name__)
INTERVAL_S = 5
_started = False

MESSAGE = ("I closed {what} — games stay locked until you've closed out work today. "
           "When you're done, tell me and I'll go over the day with you before unlocking.")


def _announced(db, day: str) -> bool:
    for row in db.execute("SELECT metadata FROM messages WHERE role='assistant' AND metadata LIKE '%\"gaming_lock\"%'"
                          ' ORDER BY created_at DESC LIMIT 20'):
        try:
            coach = json.loads(row['metadata']).get('coach') or {}
        except (TypeError, ValueError, AttributeError):
            continue
        if coach.get('kind') == 'gaming_lock' and coach.get('day') == day:
            return True
    return False


def announce(what: list[str], now: int | None = None) -> str | None:
    from backend.coach.deliver import deliver
    now = int(now if now is not None else time.time())
    day = day_key_for(now)
    db = get_db()
    if _announced(db, day):
        return None
    names = sorted(set(what))
    listed = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
    sent = deliver(MESSAGE.format(what=listed), {'coach': {'kind': 'gaming_lock', 'day': day}},
                   title='Games are locked', body=f'Closed {listed}. Tell me when work is done.',
                   category='COACH_CHECKIN', collapse_id=f'gaming-lock-{day}', now=now)
    return sent['messageId']


def tick(now: int | None = None, *, proc=processes.PROC, close=processes.close) -> list[tuple[int, str]]:
    db = get_db()
    if not lock.locked(db, now):
        return []
    found = processes.find(lock.settings(db)['patterns'], proc=proc)
    if not found:
        return []
    closed = set(close([pid for pid, _ in found]))
    hits = [(pid, name) for pid, name in found if pid in closed]
    if hits:
        logger.info('Gaming lock closed %s', ', '.join(f'{name} ({pid})' for pid, name in hits))
        try:
            announce([name for _, name in hits if name != 'wineserver'] or ['a game'], now)
        except Exception:
            logger.exception('Gaming lock could not announce itself')
    return hits


def _loop():
    wake = threading.Event()
    while True:
        try:
            tick()
        except Exception:
            logger.exception('Gaming lock tick failed')
        wake.wait(INTERVAL_S)


def start():
    global _started
    if _started or os.environ.get('LUNASCHAL_NO_SCHEDULERS') or not processes.PROC.is_dir():
        return
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name='gaming-lock').start()
