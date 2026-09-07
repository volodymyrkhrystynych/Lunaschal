"""The durable queue for background model work.

Replaces `backend/ai/background.py`'s in-memory FIFO. Same shape from a
caller's point of view — one worker, first in first out, nobody waiting on the
result — with one difference that matters: a job is a **row**, so it survives
the process.

That was a nicety while everything on the queue ran within seconds of being
enqueued. It became the point once inference could be switched off for an
evening: "queued until you turn the GPU back on" is a promise an in-memory
executor cannot keep across a restart, and the jobs it would drop are exactly
the ones whose absence is invisible — an entry that stays unpolished looks like
an entry nobody polished.

A job is data rather than a captured closure. `kind` names a handler in
backend/ai/job_handlers.py; `payload` is its arguments as JSON. A closure
cannot be written to SQLite, which is the whole reason for the registry.

**Three ways a job can end, and only one is a failure.**

- `InferencePaused` — the GPU is off. Not an error: the row stays `pending` and
  the worker stands down until it is switched back on. This is the queue doing
  its job.
- `Preempted` — an interactive call took the lane. Also not an error: the row
  stays `pending` with `cancels` incremented, and after `MAX_CANCELS` it runs
  non-preemptibly so a busy day cannot starve it forever.
- Anything else — `status='error'` with the message, which is where it stops.
  Deliberately no automatic retry: these handlers are idempotent enough to
  re-run by hand, and a failing job retried in a loop is how a broken model
  call becomes a busy loop against llama-server.
"""
import json
import logging
import threading
import time

from ulid import ULID

from backend.ai import service

logger = logging.getLogger(__name__)

# Preemptions before a job is allowed to finish uninterrupted. Three is enough
# that an ordinary busy spell just defers the work, and low enough that a job
# cannot be starved through a whole working day.
MAX_CANCELS = 3

# How long the worker sleeps with nothing to do. Short enough that a job
# enqueued by a request starts promptly, long enough not to poll SQLite hard.
IDLE_SLEEP = 2.0

# How long it waits while inference is paused. A pause is measured in hours, so
# there is nothing to gain from checking often.
PAUSED_SLEEP = 15.0

_HANDLERS: dict[str, callable] = {}

_worker: threading.Thread | None = None
_wake = threading.Event()
_stop = threading.Event()
_lock = threading.Lock()
_current: str | None = None


def handler(kind: str):
    """Register the function that runs jobs of this `kind`."""
    def register(fn):
        _HANDLERS[kind] = fn
        return fn
    return register


def known_kinds() -> set[str]:
    return set(_HANDLERS)


# ------------------------------------------------------------------ enqueueing

def enqueue(kind: str, target_id: str | None = None, payload: dict | None = None,
            *, commit: bool = True) -> str | None:
    """Queue one background model job. Returns its id, or None if already queued.

    `commit=False` leaves the insert in the caller's open transaction, so a save
    and the enrichment it implies land together — there is then no window in
    which the row exists but the work to enrich it was never recorded.
    """
    from backend.db.connection import get_db

    job_id = str(ULID())
    db = get_db()
    try:
        db.execute(
            'INSERT INTO llm_jobs (id, kind, target_id, payload, status, created_at)'
            " VALUES (?,?,?,?,'pending',?)",
            (job_id, kind, target_id, json.dumps(payload or {}), int(time.time())),
        )
    except Exception as e:
        # The partial unique index rejects a second pending job for the same
        # (kind, target). That is the intended outcome of a double tap, not a
        # failure worth propagating into the request that caused it.
        if 'UNIQUE' in str(e).upper():
            logger.debug('%s already queued for %s', kind, target_id)
            return None
        raise
    if commit:
        db.commit()
    logger.info('Queued %s job for %s (%s)', kind, target_id or '-', job_id)
    _wake.set()
    return job_id


def next_pending(db, exclude: set[str] | None = None):
    """The oldest pending job the worker has not already set aside.

    `exclude` is how a paused GPU stops blocking the CPU lane: a job that came
    back `InferencePaused` is skipped for the rest of the pause, so the worker
    walks past it to the photo captions and embeddings that can still run,
    instead of retrying the same GPU job forever.
    """
    if not exclude:
        return db.execute(
            "SELECT * FROM llm_jobs WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
    placeholders = ','.join('?' * len(exclude))
    return db.execute(
        f"SELECT * FROM llm_jobs WHERE status='pending' AND id NOT IN ({placeholders})"
        ' ORDER BY created_at LIMIT 1',
        tuple(exclude),
    ).fetchone()


def pending_count() -> int:
    from backend.db.connection import get_db
    try:
        row = get_db().execute(
            "SELECT COUNT(*) AS n FROM llm_jobs WHERE status='pending'").fetchone()
        return int(row['n']) if row else 0
    except Exception:
        return 0


# -------------------------------------------------------------------- running

def process_one(job_id: str) -> dict:
    """Run one job to completion. Never raises."""
    from backend.db.connection import get_db

    db = get_db()
    row = db.execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return {'ok': False, 'error': 'Not found'}

    fn = _HANDLERS.get(row['kind'])
    if fn is None:
        # A kind whose handler was removed or renamed. Recorded rather than
        # retried: nothing about waiting will make the handler reappear.
        _finish(db, job_id, 'error', f"No handler registered for '{row['kind']}'")
        return {'ok': False, 'error': 'no handler'}

    db.execute("UPDATE llm_jobs SET status='running', started_at=?,"
               ' attempts=attempts+1 WHERE id=?', (int(time.time()), job_id))
    db.commit()
    logger.info('Running %s job for %s (%s, attempt %d, %d cancels)',
                row['kind'], row['target_id'] or '-', job_id,
                (row['attempts'] or 0) + 1, row['cancels'] or 0)
    began = time.monotonic()

    payload = {}
    try:
        payload = json.loads(row['payload'] or '{}')
    except (TypeError, ValueError):
        pass

    # Past MAX_CANCELS this job stops yielding, so that a lane which is busy
    # all day defers it rather than cancelling it forever.
    preemptible = (row['cancels'] or 0) < MAX_CANCELS
    try:
        with service.background(preemptible=preemptible):
            fn(row['target_id'], payload)
    except service.InferencePaused:
        # Not a failure and not an attempt anybody should have to chase: the
        # switch is off. Left `pending`, and said out loud so a queue that is
        # not moving has a visible reason rather than looking stuck.
        logger.info('Deferred %s job %s: GPU inference is paused', row['kind'], job_id)
        _requeue(db, job_id, bump_cancels=False)
        return {'ok': False, 'error': 'paused'}
    except service.Preempted:
        cancels = (row['cancels'] or 0) + 1
        logger.info('Preempted %s job %s after %.1fs (cancel %d of %d); requeued',
                    row['kind'], job_id, time.monotonic() - began, cancels, MAX_CANCELS)
        _requeue(db, job_id, bump_cancels=True)
        return {'ok': False, 'error': 'preempted'}
    except Exception as e:
        logger.warning('Background job %s (%s) failed after %.1fs: %s',
                       job_id, row['kind'], time.monotonic() - began, e)
        _finish(db, job_id, 'error', str(e) or 'Failed')
        return {'ok': False, 'error': str(e)}

    logger.info('Finished %s job %s in %.1fs', row['kind'], job_id,
                time.monotonic() - began)
    _finish(db, job_id, 'done', None)
    return {'ok': True}


def _finish(db, job_id: str, status: str, error: str | None) -> None:
    db.execute('UPDATE llm_jobs SET status=?, error=?, finished_at=? WHERE id=?',
               (status, error, int(time.time()), job_id))
    db.commit()


def _requeue(db, job_id: str, *, bump_cancels: bool) -> None:
    """Back to `pending`, keeping its place by `created_at`."""
    if bump_cancels:
        db.execute("UPDATE llm_jobs SET status='pending', started_at=NULL,"
                   ' cancels=cancels+1 WHERE id=?', (job_id,))
    else:
        db.execute("UPDATE llm_jobs SET status='pending', started_at=NULL"
                   ' WHERE id=?', (job_id,))
    db.commit()


def drain_once(exclude: set[str] | None = None) -> str | None:
    """Run the oldest pending job. Returns its id, or None if there was none.

    Deliberately does *not* check the pause itself. Pausing frees the GPU, and
    the CPU lane — photo captions, audio windows, embeddings — is meant to keep
    working; short-circuiting here would stop those too. A GPU job raises
    `InferencePaused` from `slot()` and the caller sets it aside instead.
    """
    from backend.db.connection import get_db

    global _current
    row = next_pending(get_db(), exclude)
    if row is None:
        return None
    with _lock:
        _current = row['id']
    try:
        result = process_one(row['id'])
    finally:
        with _lock:
            _current = None
    if result.get('error') == 'paused' and exclude is not None:
        exclude.add(row['id'])
    return row['id']


# --------------------------------------------------------------------- worker

def _worker_loop() -> None:
    # Jobs set aside because the GPU is off. Kept per-pause rather than in the
    # DB: it is a scheduling hint, not state — the rows stay `pending`, which
    # is the durable half, and losing the hint on a restart just means one
    # wasted attempt each.
    deferred: set[str] = set()
    was_paused = False

    while not _stop.is_set():
        try:
            paused = service.is_paused()
            if was_paused and not paused:
                deferred.clear()
            was_paused = paused

            if drain_once(deferred) is None:
                # Nothing runnable. Wait longer while paused: the only thing
                # that can change is a human pressing Resume, which wakes us.
                _wake.wait(PAUSED_SLEEP if paused else IDLE_SLEEP)
                _wake.clear()
        except Exception as e:
            logger.warning('Background job worker tick failed: %s', e)
            _wake.wait(IDLE_SLEEP)
            _wake.clear()


def start_job_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_worker_loop, daemon=True,
                               name='llm-jobs')
    _worker.start()
    logger.info('Background job worker started with %d handlers; %d pending',
                len(_HANDLERS), pending_count())


def wake() -> None:
    """Nudge the worker — after a resume, or a fresh enqueue."""
    _wake.set()


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until the queue drains. True if it did, False on timeout.

    Production never needs this; tests do. These jobs use the module-global
    SQLite connection, so a suite that closes it while a job is mid-query
    segfaults the interpreter rather than raising — the same reason
    the in-memory queue this replaced grew one.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            busy = _current is not None
        if not busy and pending_count() == 0:
            return True
        time.sleep(0.02)
    return False


def reset() -> None:
    """For tests: stop the worker and forget what it was doing."""
    global _current
    _stop.set()
    _wake.set()
    with _lock:
        _current = None
    _stop.clear()
    _wake.clear()
