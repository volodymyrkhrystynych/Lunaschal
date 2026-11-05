"""The coach's loop: once a minute, do whatever is due.

- **Close finished goal periods** (`backend/goals/rollover.py`) — Monday's
  first tick after 4am grades last week's goals and asks for this week's.
- **Finalize yesterday's review** once the briefing window has passed, in case
  the briefing didn't run (inference paused, AI off, server down at 5am). The
  briefing normally does it first, and `finalize` is idempotent.
- **The evening check-in**, when switched on: at `coach_checkin_hour`, one
  message in today's chat listing what's still open and asking how the day
  went. It is a template, not a model call, so it arrives with the GPU paused.
- **Reminders that learn** (`engine.py`), when `coach_enabled` is on: plan
  today's, send or hold back the due ones, and score the ones whose window
  has closed.

Settings are read on every tick — the spoken nudge this replaces read its
switch once at startup, so turning it off did nothing until a restart.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

from backend.ai import service as ai_service
from backend.coach import config, review
from backend.coach.deliver import deliver
from backend.day_boundary import day_key_for
from backend.db.connection import get_db

logger = logging.getLogger(__name__)
_started = False


def _has_coach_message(db, conv_id: str, kind: str, day: str) -> bool:
    for row in db.execute("SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant' "
                          "AND metadata LIKE '%\"coach\"%'", (conv_id,)):
        try:
            coach = json.loads(row['metadata']).get('coach') or {}
        except (TypeError, ValueError, AttributeError):
            continue
        if coach.get('kind') == kind and coach.get('day') == day:
            return True
    return False


def checkin_text(open_items: list[dict]) -> str:
    if not open_items:
        return ("Evening check-in: everything you track is done today. "
                "Anything about the day you want to note?")
    titles = [i['title'] for i in open_items]
    listed = titles[0] if len(titles) == 1 else ', '.join(titles[:-1]) + ' and ' + titles[-1]
    return (f"Evening check-in. Still open today: {listed}.\n\n"
            "How did the day go? If something got in the way, tell me — a busy or rough day "
            "is noted as such, not counted against you.")


def post_checkin(now: int | None = None) -> str | None:
    """Today's check-in message, once. Returns its id, or None if it was
    already sent."""
    from backend.briefing_job import find_or_create_day_conversation
    now = int(now if now is not None else time.time())
    day = day_key_for(now)
    db = get_db()
    conv_id = find_or_create_day_conversation(db, day, now)
    if _has_coach_message(db, conv_id, 'checkin', day):
        db.commit()
        return None
    today = review.draft(day, now)
    open_items = [i for i in today['items'] if i['done'] is False and not i['excused'] and i['kind'] != 'todo']
    open_items += [i for i in today['items'] if i['done'] is False and not i['excused'] and i['kind'] == 'todo']
    metadata = {'coach': {'kind': 'checkin', 'day': day,
                          'open': [{'kind': i['kind'], 'id': i.get('id'), 'title': i['title']} for i in open_items]}}
    titles = [i['title'] for i in open_items]
    body = (f"Still open: {', '.join(titles[:4])}{'…' if len(titles) > 4 else ''}. How did today go?"
            if titles else 'Everything you track is done today. Anything to note?')
    sent = deliver(checkin_text(open_items), metadata, title='Evening check-in', body=body,
                   category='COACH_CHECKIN', collapse_id=f'checkin-{day}', now=now)
    return sent['messageId']


def tick(now: int | None = None) -> dict:
    """One pass. Returns what it did, for tests and the log."""
    from backend.goals import rollover
    now = int(now if now is not None else time.time())
    db = get_db()
    hour = datetime.fromtimestamp(now).hour
    did = {'rolled': 0, 'finalized': None, 'checkin': None}
    did['rolled'] = len(rollover.run(now))
    # Two hours after the briefing would have started, the same window the
    # briefing scheduler keeps. Before 4am the "yesterday" is still today's
    # predecessor's, which the 4am-day arithmetic already handles.
    if hour >= config.briefing_hour(db) + 2:
        day = review.yesterday(now)
        existing = review.get(day)
        if not existing or existing['status'] != 'final':
            with ai_service.background():
                did['finalized'] = review.finalize(day, now)['day']
    enabled, checkin_hour = config.checkin(db)
    if enabled and hour >= checkin_hour:
        did['checkin'] = post_checkin(now)
    if config.engine(db)['enabled']:
        from backend.coach import engine
        # Wording may ask the model; a chat message takes the lane back, and
        # the template is sent instead — the reminder's timing is what matters.
        with ai_service.background():
            did['planned'] = engine.plan_day(now)
            did['fired'] = len(engine.fire_due(now))
        did['resolved'] = engine.resolve(now)
    return did


def _loop():
    wake = threading.Event()
    while True:
        try:
            tick()
        except Exception:
            logger.exception('Coach tick failed')
        wake.wait(60)


def start_scheduler():
    global _started
    if _started or os.environ.get('LUNASCHAL_NO_SCHEDULERS'):
        return
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name='coach').start()
