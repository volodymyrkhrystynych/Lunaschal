"""Nightly repo-context daemon.

The fourth of the app's hand-rolled scheduler loops (see
backend/briefing_scheduler.py, which this copies). The window defaults to
03:00-05:00: the chat-title sweep owns 02:00-03:00 and the briefing owns
05:00-07:00, so this contends with neither for the two llama slots — and it
runs *before* the briefing, so a morning briefing sees a current snapshot.

The run body lives in backend/research/repo_job.py so tests can call it directly.
"""
import os
import threading
import time
from datetime import datetime

from backend.db.connection import get_db
from backend.research.repo_job import run_repo_snapshot

WINDOW_SPAN_HOURS = 2
_POLL_SECONDS = 300

DEFAULT_ENABLED = True
DEFAULT_HOUR = 3


def repo_context_settings() -> tuple[bool, int]:
    """(enabled, hour) from settings, with defaults when unset."""
    try:
        row = get_db().execute(
            'SELECT repo_context_enabled, repo_context_hour FROM settings LIMIT 1'
        ).fetchone()
    except Exception:
        return DEFAULT_ENABLED, DEFAULT_HOUR
    if not row:
        return DEFAULT_ENABLED, DEFAULT_HOUR
    enabled = bool(
        row['repo_context_enabled'] if row['repo_context_enabled'] is not None else 1
    )
    hour = row['repo_context_hour'] if row['repo_context_hour'] is not None else DEFAULT_HOUR
    return enabled, hour


def in_window(hour: int, now: datetime) -> bool:
    """True inside [hour, hour + WINDOW_SPAN_HOURS)."""
    return hour <= now.hour < hour + WINDOW_SPAN_HOURS


def should_run(enabled: bool, hour: int, now: datetime, last_run_date) -> bool:
    """The whole scheduling decision, pulled out so it is testable without
    starting a thread that has no stop signal."""
    return enabled and in_window(hour, now) and last_run_date != now.date()


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            enabled, hour = repo_context_settings()
            now = datetime.now()
            if should_run(enabled, hour, now, last_run_date):
                last_run_date = now.date()
                run_repo_snapshot()
        except Exception as e:
            print(f'Repo-context snapshot failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_repo_context_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
