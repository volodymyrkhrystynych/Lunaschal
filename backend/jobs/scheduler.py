"""The jobs module's daemon loop.

Seven jobs on three cadences, sorted by what each one costs:

- **Linkage** is pure string matching over new mail. It costs nothing, so it
  runs every tick — a rejection that landed at 09:00 shows up on the
  application by 09:05 rather than tomorrow morning.
- **Ghosting** closes submitted applications after 60 days without a linked
  reply. It runs after linkage so mail already in the inbox wins that race.
- **Sync** is network but no model. It runs every tick too, gated per search by
  its own `interval_hours`, so the tick stays cheap while a daily search stays
  daily.
- **The triage gate** is pure string work over the postings awaiting a
  verdict, so it runs every tick too and the obvious noise never survives long
  enough to cost a model call.
- **The triage drain and the queue drain** are the only parts that touch the
  model, so they are the only parts that defer through `backend/ai/priority.py`
  — the same moment-to-moment yielding `research_scheduler` does, rather than
  an hour window.
- **Retention** deletes files and only needs to be right once a day.

**Everything above except the bookkeeping stops when Settings → Jobs is
paused.** `is_paused()` is one flag read every tick, not a mass update of
`enabled` across the three source tables — a pause that rewrote 140 rows would
forget which of them the user had switched off by hand, and Resume would turn
those back on. Linkage, ghosting and retention keep running through a pause on
purpose: none of them reaches a third party or spends a llama slot, and
stopping them would quietly rot the pipeline while the user believed they had
paused only fetching.

Retention's window is **07:00–08:00**, chosen because 02:00–07:00 is already
spoken for: chat titling owns 02:00–03:00, the repo scheduler 03:00–05:00 and
the briefing 05:00–07:00, staggered so they never contend for the two llama
slots. Nothing here except the drain needs a slot, and the drain asks the
priority gate rather than the clock.
"""
import logging
import os
import threading
import time
from datetime import datetime

from backend.jobs import career_watch, linker, outcomes, queue, retention, sync, triager, workday_watch

logger = logging.getLogger(__name__)


def is_paused(db=None) -> bool:
    """Settings → Jobs → Pause. One flag, read every tick.

    A flag rather than `UPDATE ... SET enabled=0` across the three source
    tables, because a pause must be lossless: mass-toggling 140 rows forgets
    which of them the user had switched off deliberately, and Resume would
    turn those back on. Nothing per-source is written, so unpausing restores
    exactly the configuration that was there.
    """
    from backend.db.connection import get_db

    db = db if db is not None else get_db()
    row = db.execute('SELECT jobs_paused FROM settings LIMIT 1').fetchone()
    return bool(row['jobs_paused']) if row is not None else False

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
    results = {'linkage': None, 'ghosted': None, 'sync': None, 'careerWatch': None, 'workday': None, 'gated': None, 'triaged': None,
               'queued': None, 'purge': None, 'paused': False}

    # What a pause covers, and what it deliberately does not. Everything that
    # reaches a third party or spends a llama slot stops; the local bookkeeping
    # below — linkage over mail already in the database, the ghosting sweep it
    # feeds, and the retention purge — keeps running, because none of it costs
    # anything and stopping it would quietly rot the pipeline while the user
    # believed they had only paused *fetching*.
    paused = False
    try:
        paused = is_paused()
    except Exception as e:
        # A missing column on a database that has not migrated yet must not
        # take the whole tick down; not-paused is the pre-existing behaviour.
        logger.warning('Could not read the jobs pause flag: %s', e)
    results['paused'] = paused

    results['linkage'] = linker.run_linkage_sweep()
    try:
        from backend.db.connection import get_db
        results['ghosted'] = outcomes.mark_ghosted_applications(get_db())
    except Exception as e:
        logger.warning('Automatic job ghosting sweep failed: %s', e)

    # Each sweep is wrapped separately: a board that is down must not cost the
    # linkage result already computed above, nor stop the purge below.
    if not paused:
        try:
            results['sync'] = sync.run_sync_sweep()
        except Exception as e:
            logger.warning('Job sync sweep failed: %s', e)
        try:
            from backend.db.connection import get_db
            results['careerWatch'] = career_watch.run_due(get_db())
        except Exception as e:
            logger.warning('Career-page watch sweep failed: %s', e)
        try:
            from backend.db.connection import get_db
            results['workday'] = workday_watch.run_due(get_db())
        except Exception as e:
            logger.warning('Workday board sweep failed: %s', e)

        # The title gate is pure string work over pending rows, so it runs
        # every tick beside linkage and sync. Only the model half below asks
        # the gate. It stops with the rest anyway: a gate verdict is a
        # rejection the user cannot see arriving while they think jobs are
        # paused, and it is the first half of the same cascade.
        try:
            results['gated'] = triager.run_gate_sweep()
        except Exception as e:
            logger.warning('Job triage gate sweep failed: %s', e)

        try:
            results['triaged'] = triager.drain_once()
        except Exception as e:
            logger.warning('Job triage drain failed: %s', e)

        try:
            results['queued'] = queue.drain_once()
        except Exception as e:
            logger.warning('Job queue drain failed: %s', e)

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
