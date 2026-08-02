"""Interactive-first gate for the local model.

llama-server serves two 24K slots (`parallel = 2` in llama/presets.ini) off one
set of CPU threads holding the routed expert tensors. A second concurrent
generation does not *block* an interactive chat message — it gets the other
slot — but it roughly halves its token rate, because both slots contend for the
same memory-bound expert GEMVs.

So this is a throughput gate, not a mutex: background work parks *between*
steps while a human is waiting, and resumes shortly after they stop. One
generation is the finest granularity available — nothing here preempts a call
that has already started. The research worker keeps its turns short
(`max_tokens` on `chat_with_tools`) so that granularity stays small.

Two properties matter more than precision here:

- **A leaked mark must not starve background work forever.** Marks carry a
  monotonic timestamp and are pruned past `MARK_TTL`, and `wait_for_idle`
  returns False on timeout so its caller proceeds anyway. The failure mode is
  "chat feels slower for a while", never "research never runs again".
- **`end()` is idempotent and total.** It is called from a generator's
  `finally`, which runs on `GeneratorExit` when an SSE client disconnects; it
  must never raise there.
"""
import threading
import time
from contextlib import contextmanager

# A mark older than this is presumed leaked and stops counting. Long enough to
# cover a slow briefing generation, short enough that a leak costs one window
# of deferral rather than the life of the process.
MARK_TTL = 900.0

# Quiet period required after the last interactive call before background work
# resumes — without it, the gap between two chat messages looks like idleness.
IDLE_GRACE = 5.0

_lock = threading.Lock()
_cond = threading.Condition(_lock)
_marks: dict[int, tuple[str, float]] = {}
_next_token = 1
_released_at = 0.0


def _prune_locked(now: float) -> None:
    for token in [t for t, (_, at) in _marks.items() if now - at > MARK_TTL]:
        del _marks[token]


def begin(label: str = 'interactive') -> int:
    """Mark a user-initiated model call as in flight. Returns a token for end()."""
    global _next_token
    with _cond:
        token = _next_token
        _next_token += 1
        _marks[token] = (label, time.monotonic())
        return token


def end(token: int) -> None:
    """Release a mark. Idempotent — an unknown or already-pruned token is fine."""
    global _released_at
    with _cond:
        if _marks.pop(token, None) is None:
            return
        if not _marks:
            _released_at = time.monotonic()
            _cond.notify_all()


@contextmanager
def interactive(label: str = 'interactive'):
    """Scope a user-initiated model call.

    For a streaming route, prefer explicit begin() in the view and end() in the
    generator's `finally` — a `with` block cannot span the generator's lifetime.
    """
    token = begin(label)
    try:
        yield
    finally:
        end(token)


def active() -> bool:
    """True while any non-expired interactive call is in flight."""
    with _cond:
        _prune_locked(time.monotonic())
        return bool(_marks)


def snapshot() -> list[dict]:
    """[{label, age}] for the in-flight marks — for debugging a stuck gate."""
    now = time.monotonic()
    with _cond:
        _prune_locked(now)
        return [
            {'label': label, 'age': round(now - at, 1)}
            for label, at in _marks.values()
        ]


def wait_for_idle(
    timeout: float | None = 120.0,
    grace: float = IDLE_GRACE,
    cancel: threading.Event | None = None,
) -> bool:
    """Block until no interactive call has been in flight for `grace` seconds.

    Returns True when idle was reached, False on timeout or cancellation — and
    the caller is expected to proceed anyway on False. Deferring background
    work forever is a worse failure than running it while the user is busy.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if cancel is not None and cancel.is_set():
            return False
        with _cond:
            now = time.monotonic()
            _prune_locked(now)
            if not _marks:
                quiet_for = now - _released_at
                if quiet_for >= grace:
                    return True
                wait_for = grace - quiet_for
            else:
                wait_for = 1.0

            if deadline is not None:
                remaining = deadline - now
                if remaining <= 0:
                    return False
                wait_for = min(wait_for, remaining)
            # Cancellation is polled rather than waited on, so cap the sleep.
            _cond.wait(min(wait_for, 1.0))


def reset() -> None:
    """Drop all state. For tests only."""
    global _released_at
    with _cond:
        _marks.clear()
        _released_at = 0.0
        _cond.notify_all()
