"""Overnight-briefing daemon, and the life-wiki pass that feeds it.

Like the chat-title sweep (backend/chat_title_scheduler.py), there is no general
scheduler in the Flask backend — this is a small daemon thread that fires once
per local date inside an early-morning window. The window starts at or after the
4am chat-day rollover so `day_key_for()` already points at today's fresh chat and
the briefing lands there. The run bodies live in backend/briefing_job.py and
backend/lifewiki/job.py so tests can call them directly.

**Two jobs, in one thread, in order — deliberately not two daemons.** The
briefing reads the life wiki, so the wiki has to be current before it starts; two
independently scheduled loops would eventually have the briefing reading a wiki
mid-rewrite, which is the failure that shape produces. Sequencing them here makes
the ordering a fact rather than a hope, and costs no extra llama slot: the two
run one after the other inside a window nothing else uses.

**The wiki pass is bounded and the briefing runs regardless.** Half a wiki plus a
briefing beats a complete wiki and no briefing, so the pass carries a wall-clock
budget and its failure is logged rather than raised.
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


def run_nightly() -> None:
    """The life-wiki pass, then the briefing. Order is the point.

    A failing wiki pass must not cost the briefing: the briefing is the thing
    the user wakes up to, and a picture one night out of date is a far smaller
    loss than no plan for the day. So the pass is wrapped separately from the
    call it exists to improve.
    """
    import time as _time

    from backend.lifewiki.job import DEFAULT_BUDGET_SECONDS, run_life_wiki_pass

    try:
        result = run_life_wiki_pass(
            deadline=_time.monotonic() + DEFAULT_BUDGET_SECONDS
        )
        if result.get('facts') or result.get('articles'):
            print(f'Life-wiki pass: {result["facts"]} facts, '
                  f'{result["articles"]} articles, '
                  f'{result["observationsFolded"]} notes filed'
                  + (' (hit its budget)' if result.get('timedOut') else ''))
    except Exception as e:
        print(f'Life-wiki pass failed, briefing continuing: {e}')

    run_briefing()


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            enabled, hour = _briefing_settings()
            now = datetime.now()
            in_window = hour <= now.hour < hour + WINDOW_SPAN_HOURS
            if enabled and in_window and last_run_date != now.date():
                last_run_date = now.date()
                run_nightly()
        except Exception as e:
            print(f'Overnight briefing failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_briefing_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
