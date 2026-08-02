"""The loop that keeps the research worker fed.

Unlike the other four daemons in this app, this one has **no hour window**. The
repo scan, the briefing and the title sweep are scheduled at night because they
should not compete with the user; this one instead defers moment to moment via
backend/ai/priority.py, which is what "runs whenever it likes but yields to
anything the user asks for" actually requires. A nightly window would be the
wrong shape: research is long, interruptible, and worth doing while the user is
awake and about to look at the answer.

So each tick is a question, not a schedule: is the feature on, is the worker
free, has the user been quiet for a moment, and is anything due? If all four,
submit exactly one task and go back to sleep.
"""
import os
import threading
import time

from backend.db.connection import get_db

_POLL_SECONDS = 120
# How long the user must have been idle before background work starts. Longer
# than priority.IDLE_GRACE because starting a multi-minute research pass the
# instant a chat finishes is more intrusive than starting one model call.
QUIET_SECONDS = 30.0

DEFAULT_ENABLED = False


def research_enabled() -> bool:
    try:
        row = get_db().execute('SELECT research_enabled FROM settings LIMIT 1').fetchone()
    except Exception:
        return DEFAULT_ENABLED
    if not row or row['research_enabled'] is None:
        return DEFAULT_ENABLED
    return bool(row['research_enabled'])


def tick(now: int | None = None) -> tuple[str, str] | None:
    """One pass. Returns the task submitted, or None.

    Split out from the loop so the whole decision is testable without threads —
    the loop below is then only sleep-and-call.
    """
    from backend.ai import priority
    from backend.research import worker
    from backend.research.research_job import plan_next, run_task

    if not research_enabled():
        return None
    if worker.status()['running']:
        return None
    # Don't queue behind the user. Returning rather than blocking keeps the
    # poll interval meaningful and lets `enabled` be re-read next tick.
    if priority.active() or priority.idle_seconds() < QUIET_SECONDS:
        return None

    task = plan_next(now)
    if task is None:
        return None

    kind, idea_id = task
    submitted = worker.submit(
        kind, lambda cancel: run_task(kind, idea_id, cancel), target=idea_id
    )
    return task if submitted else None


def _scheduler_loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:
            print(f'Research scheduler failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_research_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
