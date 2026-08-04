"""The research executor.

Deliberately NOT `backend.ai.background.run_bg`. That is a single FIFO worker
shared by journal polish, journal metadata, attachment transcription, food
structuring, workout parsing and learning-attempt grading — all triggered by
something the user did seconds ago. A research pass is minutes of tool turns;
putting it on that queue would head-of-line block every one of those flows,
with no way to jump the queue because ThreadPoolExecutor has no priority and
run_bg discards the Future so nothing can even be cancelled.

So: its own single worker, plus a cancel Event, plus an in-memory progress
registry — the same arrangement the fanfic downloader uses, and for the same
reason (a long job the user needs to be able to watch and stop).

One rule for anything running here: **never hold a transaction across a model
or tool call.** `get_db()` hands out one process-global connection, so a
`commit()` in any Flask request handler would commit whatever this thread has
pending. Write, commit, then call the model.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='research')
_lock = threading.Lock()
_cancel = threading.Event()
_current: dict | None = None
_last: dict | None = None

# A second research thread would only double the slot contention the priority
# gate exists to avoid.
MAX_WORKERS = 1


def status() -> dict:
    """What the worker is doing, for the UI to poll."""
    with _lock:
        return {
            'running': _current is not None,
            'current': dict(_current) if _current else None,
            'last': dict(_last) if _last else None,
        }


def cancel() -> bool:
    """Ask the running job to stop at its next checkpoint. False if idle."""
    with _lock:
        if _current is None:
            return False
    _cancel.set()
    return True


def is_cancelled() -> bool:
    return _cancel.is_set()


def _finish(kind: str, target: str | None, started: float,
            error: str | None, cancelled: bool) -> None:
    # Both globals, or `_current = None` below silently binds a local and the
    # registry never clears — the worker then looks busy forever and refuses
    # every subsequent job.
    global _current, _last
    with _lock:
        _last = {
            'kind': kind,
            'target': target,
            'error': error,
            'cancelled': cancelled,
            'seconds': round(time.monotonic() - started, 1),
            'finishedAt': int(time.time()),
        }
        _current = None


def submit(kind: str, fn, target: str | None = None) -> bool:
    """Queue a research job. False when one is already running.

    `fn` is called with a `cancel` Event and is expected to pass it to
    `agent.make_checkpoint` so it stops between steps rather than mid-turn.
    """
    global _current
    with _lock:
        if _current is not None:
            return False
        _current = {'kind': kind, 'target': target, 'startedAt': int(time.time())}
    _cancel.clear()

    def _run():
        from backend.research.agent import Cancelled
        started = time.monotonic()
        error = None
        cancelled = False
        try:
            fn(_cancel)
        except Cancelled:
            cancelled = True
        except Exception as e:
            # Swallowed like every other background body in this app: there is
            # no caller left to raise to, and losing the worker would silently
            # end all future research.
            error = str(e)
            logger.warning('Research job %s failed: %s', kind, e)
        finally:
            _finish(kind, target, started, error, cancelled)

    _executor.submit(_run)
    return True


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
    _cancel.clear()
