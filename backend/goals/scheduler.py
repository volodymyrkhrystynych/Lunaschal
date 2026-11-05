"""Restart-safe polling: all progress and source fingerprints live in SQLite."""
import logging
import os
import threading

from backend.ai import service as ai_service
from backend.goals import service

logger = logging.getLogger(__name__)
_started = False


def sweep():
    ai_service.clear_deferral()
    with ai_service.background():
        for goal in service.list_goals():
            if goal['status'] not in ('cancelled', 'redeemed'):
                service.evaluate(goal['id'], retry=False)
                if ai_service.deferred_reason():
                    break


def _loop():
    wake = threading.Event()
    while True:
        try:
            sweep()
        except Exception:
            logger.exception('Goal progress check failed')
        wake.wait(60)


def start_scheduler():
    global _started
    if _started or os.environ.get('LUNASCHAL_NO_SCHEDULERS'):
        return
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name='goal-progress').start()
