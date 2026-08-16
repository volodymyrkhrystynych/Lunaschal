"""The jobs module's daemon loop.

Two jobs on two very different cadences, for one reason: the linkage sweep is
pure string matching and costs nothing, while the purge sweep deletes files and
only needs to be right once a day.

So linkage runs every tick — a rejection that landed at 09:00 shows up on the
application by 09:05 rather than tomorrow morning — and retention runs once
daily inside a narrow window. That window is **07:00–08:00**, chosen because
02:00–07:00 is already spoken for: chat titling owns 02:00–03:00, the repo
scheduler 03:00–05:00 and the briefing 05:00–07:00, staggered so they never
contend for the two llama slots. This loop needs no slot at all, but keeping
out of their window costs nothing and keeps the schedule readable.
"""
import logging
import os
import threading
import time
from datetime import datetime

from backend.jobs import linker, retention

logger = logging.getLogger(__name__)

# Local-hour window the daily purge is allowed to fire in: [start, end).
PURGE_WINDOW_START_HOUR = 7
PURGE_WINDOW_END_HOUR = 8

_POLL_SECONDS = 300


def tick(now: datetime | None = None, last_purge_date=None):
    """One scheduler pass. Returns (results, new_last_purge_date).

    Split out from the loop so tests drive it directly, the way
    `run_title_sweep` is.
    """
    now = now or datetime.now()
    results = {'linkage': None, 'purge': None}

    results['linkage'] = linker.run_linkage_sweep()

    in_window = PURGE_WINDOW_START_HOUR <= now.hour < PURGE_WINDOW_END_HOUR
    if in_window and last_purge_date != now.date():
        last_purge_date = now.date()
        results['purge'] = retention.run_purge_sweep()

    return results, last_purge_date


def _scheduler_loop() -> None:
    last_purge_date = None
    while True:
        try:
            _, last_purge_date = tick(last_purge_date=last_purge_date)
        except Exception as e:
            # Never let one bad pass kill the thread — there is no supervisor
            # to restart it, and a dead loop is silent.
            logger.warning('Jobs scheduler tick failed: %s', e)
        time.sleep(_POLL_SECONDS)


def start_jobs_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
