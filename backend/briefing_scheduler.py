"""Overnight-briefing daemon.

Like the chat-title sweep (backend/chat_title_scheduler.py), there is no general
scheduler in the Flask backend — this is a small daemon thread that fires once
per local date inside an early-morning window. The window starts at or after the
4am chat-day rollover so `day_key_for()` already points at today's fresh chat and
the briefing lands there. The run body lives in backend/briefing_job.py so tests
can call it directly.
"""
import os
import threading
import time
from datetime import datetime

from backend.db.connection import get_db
from backend.briefing_job import run_briefing

# The window is [hour, hour + WINDOW_SPAN_HOURS); poll interval below.
WINDOW_SPAN_HOURS = 2
_POLL_SECONDS = 300


def _briefing_settings() -> tuple[bool, int]:
    """(enabled, hour) from settings, with defaults when unset."""
    try:
        row = get_db().execute(
            'SELECT briefing_enabled, briefing_hour FROM settings LIMIT 1'
        ).fetchone()
    except Exception:
        return True, 5
    if not row:
        return True, 5
    enabled = bool(row['briefing_enabled'] if row['briefing_enabled'] is not None else 1)
    hour = row['briefing_hour'] if row['briefing_hour'] is not None else 5
    return enabled, hour


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            enabled, hour = _briefing_settings()
            now = datetime.now()
            in_window = hour <= now.hour < hour + WINDOW_SPAN_HOURS
            if enabled and in_window and last_run_date != now.date():
                last_run_date = now.date()
                run_briefing()
        except Exception as e:
            print(f'Overnight briefing failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_briefing_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
