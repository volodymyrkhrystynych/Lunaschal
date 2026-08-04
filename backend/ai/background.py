"""The shared single-worker queue for deferred AI work.

One FIFO worker, shared by journal polish, journal metadata, attachment
transcription, food structuring, workout parsing and learning-attempt grading.
Everything here was triggered by something the user did seconds ago, so it is
interactive in spirit even though it runs off-request — hence the priority mark
below, which keeps the Ideas research worker from competing with it for a llama
slot.

Long agent runs deliberately do NOT go here: they would head-of-line block
every one of the flows above for minutes. They get their own executor in
backend/research/worker.py.
"""
import threading
from concurrent.futures import ThreadPoolExecutor, wait

from backend.ai import priority

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ai-bg')
_pending: set = set()
_lock = threading.Lock()


def run_bg(fn) -> None:
    def _marked():
        with priority.interactive('bg'):
            fn()

    future = _executor.submit(_marked)
    with _lock:
        _pending.add(future)
    future.add_done_callback(_forget)


def _forget(future) -> None:
    with _lock:
        _pending.discard(future)


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until queued work drains. True if it did, False on timeout.

    Production never needs this — the queue outlives the process. Tests do:
    these jobs read the module-global SQLite connection, so a suite that closes
    it while a job is mid-query segfaults the interpreter rather than raising.
    """
    with _lock:
        pending = list(_pending)
    if not pending:
        return True
    return not wait(pending, timeout=timeout).not_done
