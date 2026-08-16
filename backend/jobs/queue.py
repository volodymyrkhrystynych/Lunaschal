"""The resume backburner.

Queueing a posting on the phone should return instantly and cost nothing —
tapping Queue while standing on a bus is a judgement, not a request to wait
thirty seconds for a model. So `POST /api/jobs/<id>/queue` writes `queued_at`
and returns, and this worker does the slow half: tailor, render PDF, render
DOCX, mark `ready`.

Deliberately its own single-slot worker rather than `backend.ai.background`'s
shared FIFO, for the reason `backend/research/worker.py` gives at length: a
queue of twenty resumes on that executor would head-of-line block journal
polish, food structuring and every other seconds-after-a-tap flow.

Two rules it lives by:

- **Never hold a transaction across the model call.** `get_db()` is one
  process-global connection, so a `commit()` in any Flask handler commits
  whatever this thread left pending. Read inputs, commit, call the model,
  commit the result.
- **A failure is recorded, not swallowed.** `queue_error` is written to the
  application so a resume that never generated is visible in the UI instead of
  sitting in `draft` looking like the user forgot about it.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='jobqueue')
_lock = threading.Lock()
_current: dict | None = None
_last: dict | None = None

MAX_WORKERS = 1

# How long the user must have been quiet before a tailoring pass starts. Same
# reasoning as research_scheduler.QUIET_SECONDS: one model call is fine to
# start the moment a chat ends, a multi-minute batch is not.
QUIET_SECONDS = 20.0


def status() -> dict:
    with _lock:
        return {
            'running': _current is not None,
            'current': dict(_current) if _current else None,
            'last': dict(_last) if _last else None,
        }


def _finish(application_id: str, started: float, error: str | None) -> None:
    # Both globals — binding `_current` as a local here would leave the worker
    # looking permanently busy and refusing every later job.
    global _current, _last
    with _lock:
        _last = {
            'applicationId': application_id,
            'error': error,
            'seconds': round(time.monotonic() - started, 1),
            'finishedAt': int(time.time()),
        }
        _current = None


def next_queued(db) -> dict | None:
    """The oldest queued application still waiting for a resume.

    Ordered by `queued_at` so the queue is a queue. `status='draft'` is the
    other half of the condition: once the worker succeeds the row becomes
    'ready' and stops matching, which is what makes this safe to re-run.

    **A failed application is skipped until it is re-queued.** Without that,
    one posting the model cannot handle sits at the head of the queue and is
    retried every five minutes forever, and nothing behind it is ever built.
    Re-queueing from the UI clears `queue_error`, which is the explicit retry.
    """
    row = db.execute(
        """SELECT a.id, a.job_id, a.steer
           FROM applications a
           WHERE a.queued_at IS NOT NULL
             AND a.status = 'draft'
             AND a.queue_error IS NULL
             AND NOT EXISTS (
                 SELECT 1 FROM resume_versions rv WHERE rv.application_id = a.id
             )
           ORDER BY a.queued_at
           LIMIT 1"""
    ).fetchone()
    return {'id': row['id'], 'jobId': row['job_id'], 'steer': row['steer']} if row else None


def process_one(application_id: str) -> dict:
    """Tailor and render one queued application. Never raises.

    Returns {'ok': bool, 'error': str|None}. Called on the worker thread, and
    directly by tests.
    """
    from backend.db.connection import get_db
    from backend.jobs.build import build_resume_version

    db = get_db()
    started = time.monotonic()
    try:
        # build_resume_version already flips 'draft' to 'ready' and clears
        # queue_error, so there is nothing to stamp here on success.
        version = build_resume_version(db, application_id)
        return {'ok': True, 'error': None, 'versionId': version.get('id')}
    except Exception as e:
        logger.warning('Queued resume for %s failed: %s', application_id, e)
        try:
            db.execute(
                'UPDATE applications SET queue_error=?, updated_at=? WHERE id=?',
                (str(e), int(time.time()), application_id),
            )
            db.commit()
        except Exception:
            # The error write is best-effort; losing it must not lose the
            # worker, which would silently end all future queue processing.
            logger.exception('Could not record queue_error for %s', application_id)
        return {'ok': False, 'error': str(e)}
    finally:
        logger.debug('Queue pass for %s took %.1fs', application_id,
                     time.monotonic() - started)


def submit(application_id: str) -> bool:
    """Run one application through the worker. False when already busy."""
    global _current
    with _lock:
        if _current is not None:
            return False
        _current = {'applicationId': application_id, 'startedAt': int(time.time())}

    def _run():
        started = time.monotonic()
        error = None
        try:
            result = process_one(application_id)
            error = result.get('error')
        except Exception as e:
            error = str(e)
            logger.warning('Queue worker crashed on %s: %s', application_id, e)
        finally:
            _finish(application_id, started, error)

    _executor.submit(_run)
    return True


def drain_once() -> str | None:
    """Scheduler entry point: submit at most one queued application.

    Returns the id submitted, or None. Every gate is a question rather than a
    block, so the tick stays cheap and the answers are re-read next time.
    """
    from backend.ai import priority
    from backend.db.connection import get_db

    with _lock:
        if _current is not None:
            return None
    # Don't queue behind the user — a tailoring pass is minutes of the model.
    if priority.active() or priority.idle_seconds() < QUIET_SECONDS:
        return None

    pending = next_queued(get_db())
    if pending is None:
        return None
    return pending['id'] if submit(pending['id']) else None


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until no job is running. For tests and shutdown."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            if _current is None:
                return True
        time.sleep(0.01)
    return False


def reset() -> None:
    """Drop registry state. For tests only."""
    global _current, _last
    with _lock:
        _current = None
        _last = None
