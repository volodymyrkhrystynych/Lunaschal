"""The one service that decides which model calls run, and when.

Every request to llama-server passes through `slot()`. Before this module there
were seven independent request sites and an *advisory* gate (the retired
`backend/ai/priority.py`) that only some callers consulted, which is how the
briefing pass came to spend forty-five minutes on the card without asking
anyone, and how `backend/ai/background.py` ended up marking its whole queue
`interactive` — so background enrichment starved the workers that did defer.

Three ideas, in order of how much they explain:

**Two priorities.** P1 is a call somebody is waiting on: a chat message, a
tapped button. P2 is work whose input is already saved durably — polish,
metadata, the nightly passes — so nobody notices if it happens in ten seconds or
ten minutes. P1 always goes first; within a priority it is FIFO. A P2 is
admitted only when no P1 is running *or waiting*, so a burst of chat messages
does not get interleaved with background work.

**Two lanes.** `[qwen36]` is the only preset on the GPU (`n-gpu-layers = 999`);
`[gemma4-12b-omni]` and `[embed]` set it to 0 and run on the CPU. They contend
for different things, so they queue separately: a photo caption never waits
behind a nightly briefing, and pausing the GPU leaves captioning and embeddings
working. This is not free — the CPU presets still share the 8 threads that hold
qwen36's routed experts (`threads = 8` in llama/presets.ini) — so freeing the
GPU is not freeing the machine, and the lanes are about *queueing* rather than
about isolation.

**Preemption is real, not advisory.** When a P1 arrives and a P2 is generating,
we set that P2's cancel event; the caller closes its HTTP stream, and
llama-server responds to the client disconnect by posting a cancel task to the
*front* of its own queue (`tools/server/server-queue.cpp`, `~server_response_reader`
-> `stop()`), which genuinely frees the slot. That is what makes throwing the
work away worth it: the GPU is handed back within a token, not at the end of the
generation. The cost is that a P2 can be killed repeatedly on a busy day, so
`preemptible=False` exists for a caller that has been cancelled too often and
needs to finish (see backend/ai/jobs.py's cancel counter).

The lane is derived from the resolved **alias**, never from the feature, because
`backend/ai/images.py`'s `_repoint_vision_at_qwen36` can put the vision path on
the chat model — at which point it is GPU work and must be gated like it. If a
future preset is placed on the card, it has to join `_GPU_ALIASES`.
"""
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from enum import IntEnum

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Lower runs first. Deliberately ints, so ordering is the obvious thing."""
    INTERACTIVE = 1   # someone is waiting on this result
    BACKGROUND = 2    # input is already saved; safe to cancel and redo


class Preempted(Exception):
    """A P2 was cancelled to hand the lane to a P1.

    Named for the cause rather than `Cancelled`, which
    `backend/research/agent.py` already uses for a user-cancelled research run —
    two exceptions called the same thing, meaning different things, in call
    stacks that reach each other, is a debugging trap.
    """


class InferencePaused(Exception):
    """The GPU lane is switched off so the card can be used for something else.

    P1 callers surface this as a 503; P2 callers leave their job row `pending`.
    """


# The one wording for a paused refusal, so the SSE frames and the HTTP error
# handler cannot drift into saying different things about the same state.
PAUSED_MESSAGE = 'GPU inference is paused — resume it in Settings to run this.'

GPU = 'gpu'
CPU = 'cpu'

# How many generations a lane may run at once. The GPU lane's 2 is not a guess:
# it is `parallel = 2` on [qwen36], which exists so a background structured call
# never queues behind an interactive chat message. The CPU lane's 2 is the two
# distinct CPU presets (omni and embed), each with one slot of its own.
_LANE_SLOTS = {GPU: 2, CPU: 2}

# An entry older than this is presumed leaked and stops counting against
# capacity. `slot()` releases in a `finally`, so this should never fire — but
# the failure it guards against is "background work never runs again", which is
# far worse than briefly over-subscribing a lane.
ENTRY_TTL = 1800.0

# How long a cached read of the pause flag stays good. The flag is read on every
# model call and changes only when a human presses a button, so a couple of
# seconds of staleness costs nothing and saves a SQLite read per call.
_PAUSE_TTL = 2.0


class _Entry:
    __slots__ = ('seq', 'priority', 'lane', 'label', 'cancel', 'preemptible', 'at')

    def __init__(self, seq, priority, lane, label, preemptible):
        self.seq = seq
        self.priority = priority
        self.lane = lane
        self.label = label
        self.preemptible = preemptible
        self.cancel = threading.Event()
        self.at = time.monotonic()


class _Lane:
    def __init__(self, slots):
        self.slots = slots
        self.running: list[_Entry] = []
        self.waiting: list[_Entry] = []
        # When the last interactive call on this lane finished. 0.0 means none
        # ever has. Feeds `idle_seconds`, which is how a scheduler asks "has the
        # user stopped" before committing to a long background job.
        self.released_at = 0.0


_cond = threading.Condition()
_lanes = {GPU: _Lane(_LANE_SLOTS[GPU]), CPU: _Lane(_LANE_SLOTS[CPU])}
_next_seq = 1

_pause_cache: tuple[bool, float] | None = None
_pause_lock = threading.Lock()

# Priority is a property of the *thread*, not of each call.
#
# A background job is P2 as a whole — the polish, the metadata pass, the
# nightly briefing — and it reaches the model through four or five layers of
# feature code that has no business knowing about priorities. Threading a
# `priority=` kwarg down every one of those call paths would touch some forty
# sites and be wrong the first time somebody added a fifth. So the worker wraps
# the job in `background()` and every model call that job makes inherits it.
#
# The default is INTERACTIVE deliberately: a call from a thread nobody
# classified is a request handler, and treating an unclassified call as
# background would silently defer a user who is waiting.
_local = threading.local()


# ------------------------------------------------------------- the activity log

# What Settings -> Logs reads back. A ring buffer rather than a table, because
# this answers a question that is only ever asked live — "what has the service
# been doing for the last few minutes, and why did that call not happen" — and a
# row per model call would put thousands of writes a day on SQLite to answer it.
#
# Every event here is *also* emitted through `logger`, so the same story reaches
# journald and survives a restart. The buffer is the copy that can be read from
# the phone: a dev run has no `systemd --user` journal at all, and production's
# has this service's lines interleaved with everything else the app prints.
EVENT_LIMIT = 300

_events: deque = deque(maxlen=EVENT_LIMIT)
_events_lock = threading.Lock()

# Totals since the process started, so a glance says whether anything is
# systematically wrong without reading three hundred lines. Reset only by
# `reset()`.
_counters = {
    'calls': 0,        # slots that ran to completion, whatever the outcome
    'preempted': 0,    # background calls asked to stop for an interactive one
    'refused': 0,      # calls turned away because the GPU lane is paused
    'errors': 0,       # calls whose body raised something that was not either
    'leaked': 0,       # slots dropped by the TTL sweep — should stay at 0
}


def _record(kind: str, **fields) -> None:
    """Append one line to the activity log. Never raises, never blocks long.

    `_events_lock` is always the innermost lock — nothing under it touches
    `_cond` — so recording from inside an admission decision cannot deadlock.
    """
    with _events_lock:
        _events.append({'at': time.time(), 'kind': kind, **fields})
        if kind == 'call':
            _counters['calls'] += 1
            if fields.get('outcome') == 'error':
                _counters['errors'] += 1
        elif kind == 'preempt':
            _counters['preempted'] += 1
        elif kind == 'refused':
            _counters['refused'] += 1
        elif kind == 'leak':
            _counters['leaked'] += 1


def note(kind: str, detail: str | None = None, **fields) -> None:
    """Record something that happened *to* the service rather than in it.

    The pause switch is the caller. Without it the log reads as the app
    inexplicably declining to work; with it, the run of refusals underneath has
    an obvious cause sitting one line above.
    """
    logger.info('inference %s%s', kind, f': {detail}' if detail else '')
    _record(kind, detail=detail, **fields)


def recent_events(limit: int = 100) -> list[dict]:
    """Newest first, so the end that matters is not `limit` rows down."""
    with _events_lock:
        events = list(_events)
    events.reverse()
    return events[:max(0, limit)]


def counters() -> dict:
    with _events_lock:
        return dict(_counters)


# ---------------------------------------------------------------- lane routing

def _gpu_aliases() -> set[str]:
    """Aliases served from VRAM. Everything else is CPU-resident by preset."""
    from backend.ai.provider import get_model
    try:
        return {get_model()}
    except Exception:
        from backend.ai.provider import DEFAULT_MODEL
        return {DEFAULT_MODEL}


def lane_for(model: str | None) -> str:
    """Which queue a call on `model` belongs in."""
    return GPU if model in _gpu_aliases() else CPU


# --------------------------------------------------------------- pause switch

def is_paused() -> bool:
    """Whether the GPU lane is switched off. Never raises."""
    global _pause_cache
    now = time.monotonic()
    with _pause_lock:
        if _pause_cache is not None and now - _pause_cache[1] < _PAUSE_TTL:
            return _pause_cache[0]
    try:
        from backend.db.connection import get_db
        row = get_db().execute(
            'SELECT inference_paused FROM settings LIMIT 1').fetchone()
        value = bool(row['inference_paused']) if row else False
    except Exception:
        # A missing column or an unopened DB means "not paused" — refusing every
        # model call because a settings read failed would be a far worse bug
        # than running while the user wanted the card free.
        value = False
    with _pause_lock:
        _pause_cache = (value, now)
    return value


def invalidate_pause_cache() -> None:
    """Drop the cached flag so the next read hits the DB. Call after writing it."""
    global _pause_cache
    with _pause_lock:
        _pause_cache = None


# ----------------------------------------------------- per-thread priority

def current_priority() -> Priority:
    return getattr(_local, 'priority', Priority.INTERACTIVE)


def current_preemptible() -> bool:
    return getattr(_local, 'preemptible', True)


@contextmanager
def background(*, preemptible: bool = True):
    """Mark this thread's model calls as P2 for the duration.

    `preemptible=False` is the escape hatch for a job that has been cancelled so
    often it would otherwise never finish — see the cancel counter in
    backend/ai/jobs.py. Nested use restores the previous value rather than
    resetting to interactive, so a job that calls a helper which also marks
    itself background does not come out of it as P1.
    """
    prev = (current_priority(), current_preemptible())
    _local.priority = Priority.BACKGROUND
    _local.preemptible = preemptible
    try:
        yield
    finally:
        _local.priority, _local.preemptible = prev


@contextmanager
def interactive():
    """Mark this thread's model calls as P1 for the duration.

    Needed where a user-initiated call runs on a thread the worker already
    marked background — the manual retry path, which re-runs a P2 job body.
    """
    prev = (current_priority(), current_preemptible())
    _local.priority = Priority.INTERACTIVE
    _local.preemptible = True
    try:
        yield
    finally:
        _local.priority, _local.preemptible = prev


# ------------------------------------------------------------------ admission

def _live(lane: _Lane, now: float) -> list[_Entry]:
    """Running entries that have not outlived `ENTRY_TTL`."""
    fresh = [e for e in lane.running if now - e.at <= ENTRY_TTL]
    if len(fresh) != len(lane.running):
        for e in lane.running:
            if e not in fresh:
                logger.warning('Dropping leaked %s slot after %.0fs: %s',
                               e.lane, now - e.at, e.label)
                _record('leak', lane=e.lane, label=e.label,
                        priority=int(e.priority), age=round(now - e.at, 1))
        lane.running = fresh
    return fresh


def _next_admissible(lane: _Lane, now: float) -> _Entry | None:
    """The one waiter that may start, or None. Caller holds `_cond`."""
    if not lane.waiting:
        return None
    lane.waiting.sort(key=lambda e: (e.priority, e.seq))
    candidate = lane.waiting[0]
    running = _live(lane, now)
    if len(running) >= lane.slots:
        return None
    if candidate.priority == Priority.INTERACTIVE:
        # A P1 takes a free slot even next to a P2 that is still unwinding, or
        # next to one that has escalated to non-preemptible. Sharing a lane at
        # half speed beats making the user wait for a generation to finish.
        return candidate
    # A P2 candidate implies no P1 is waiting (the sort puts P1 first), so the
    # only question left is whether one is in flight.
    if any(e.priority == Priority.INTERACTIVE for e in running):
        return None
    return candidate


def _preempt_locked(lane: _Lane, by: str) -> None:
    """Ask every preemptible P2 in flight to stop. Caller holds `_cond`.

    `by` is the label of the interactive call that arrived. It is only ever used
    for the log line, and it is the whole value of that line: "polish was
    cancelled" is a mystery, "polish was cancelled for chat" is an explanation.
    """
    for e in lane.running:
        if e.priority == Priority.BACKGROUND and e.preemptible and not e.cancel.is_set():
            logger.info('Preempting background %s for interactive %s', e.label, by)
            _record('preempt', lane=e.lane, label=e.label, by=by,
                    ran=round(time.monotonic() - e.at, 2))
            e.cancel.set()


@contextmanager
def slot(*, lane: str, label: str, priority: Priority | None = None,
         preemptible: bool | None = None):
    """Hold a lane slot for one model call.

    Yields a `threading.Event` that is set if this call is preempted; the caller
    must watch it and abandon its HTTP request when it fires. Blocks until
    admitted. Raises `InferencePaused` immediately if the GPU lane is off — for
    both priorities, since a P1 wants to tell the user and a P2 wants to stay
    queued in the DB, and neither is served by waiting here.

    `priority` defaults to the calling thread's (see `background()`).
    """
    global _next_seq

    if priority is None:
        priority = current_priority()
    if preemptible is None:
        preemptible = current_preemptible()

    if lane == GPU and is_paused():
        logger.info('Refused %s: GPU inference is paused', label)
        _record('refused', lane=lane, label=label, priority=int(priority))
        raise InferencePaused('GPU inference is paused')

    with _cond:
        entry = _Entry(_next_seq, priority, lane, label, preemptible)
        _next_seq += 1
        lane_state = _lanes[lane]
        lane_state.waiting.append(entry)
        if priority == Priority.INTERACTIVE:
            _preempt_locked(lane_state, label)
        _cond.notify_all()
        try:
            while _next_admissible(lane_state, time.monotonic()) is not entry:
                _cond.wait(1.0)
        except BaseException:
            # A waiter abandoned mid-queue — KeyboardInterrupt, a timeout in a
            # test, a killed thread — must take its place in the line with it.
            # `waiting` has no TTL sweep the way `running` does, so an entry
            # left at the head would block the lane for the life of the
            # process: every other waiter checks whether *it* is next, and it
            # never would be.
            if entry in lane_state.waiting:
                lane_state.waiting.remove(entry)
            _cond.notify_all()
            raise
        lane_state.waiting.remove(entry)
        admitted = time.monotonic()
        waited = admitted - entry.at
        entry.at = admitted
        lane_state.running.append(entry)

    # Queueing is invisible from the outside — a chat reply that took nine
    # seconds looks identical whether the model was slow or the call spent eight
    # of them behind something else. Logged only when it was long enough to be
    # the answer to that question.
    if waited >= 1.0:
        logger.info('%s waited %.1fs for a %s slot', label, waited, lane)

    outcome = 'ok'
    detail = None
    try:
        yield entry.cancel
    except Preempted:
        outcome = 'preempted'
        raise
    except InferencePaused:
        # The lane was paused mid-call — the stream was already open when the
        # switch went down. Distinct from `refused`, which never started.
        outcome = 'paused'
        raise
    except GeneratorExit:
        # A streaming caller was closed before it finished — an SSE client that
        # navigated away, most of the time. `chat_stream_events` holds its slot
        # across the whole generator, so this is the ordinary end of a chat the
        # user walked away from, and counting it as a failure would put a red
        # line in the log for something nothing is wrong with.
        outcome = 'abandoned'
        raise
    except BaseException as e:
        outcome = 'error'
        detail = f'{type(e).__name__}: {e}'[:200]
        raise
    finally:
        ran = time.monotonic() - entry.at
        with _cond:
            state = _lanes[lane]
            # Idempotent: a leak sweep may already have dropped this entry.
            if entry in state.running:
                state.running.remove(entry)
            if entry.priority == Priority.INTERACTIVE and not any(
                    e.priority == Priority.INTERACTIVE for e in state.running):
                state.released_at = time.monotonic()
            _cond.notify_all()
        _record('call', lane=lane, label=label, priority=int(priority),
                waited=round(waited, 2), ran=round(ran, 2),
                outcome=outcome, detail=detail)
        if outcome == 'error':
            logger.warning('%s call %s failed after %.1fs: %s',
                           lane, label, ran, detail)
        else:
            logger.debug('%s call %s finished %s in %.1fs (waited %.1fs)',
                         lane, label, outcome, ran, waited)


def interactive_active(lane: str = GPU) -> bool:
    """Whether a P1 call is running or queued on `lane`.

    The broker already orders admission, so a background worker does not need
    this to stay out of the way. It is for the coarser question a scheduler
    asks before *starting* a multi-minute job: launching one the instant a chat
    message lands only to have it preempted a second later is churn, not
    politeness.
    """
    now = time.monotonic()
    with _cond:
        state = _lanes[lane]
        return (any(e.priority == Priority.INTERACTIVE for e in _live(state, now))
                or any(e.priority == Priority.INTERACTIVE for e in state.waiting))


def idle_seconds(lane: str = GPU) -> float:
    """Seconds since the last interactive call on `lane` finished.

    0.0 while one is in flight or queued, `inf` when none has ever run.
    """
    now = time.monotonic()
    with _cond:
        state = _lanes[lane]
        if (any(e.priority == Priority.INTERACTIVE for e in _live(state, now))
                or any(e.priority == Priority.INTERACTIVE for e in state.waiting)):
            return 0.0
        if state.released_at == 0.0:
            return float('inf')
        return now - state.released_at


def status() -> dict:
    """Queue depths and what is in flight, for the Settings panel."""
    now = time.monotonic()
    with _cond:
        out = {}
        for name, lane in _lanes.items():
            running = _live(lane, now)
            out[name] = {
                'slots': lane.slots,
                'running': [
                    {'label': e.label, 'priority': int(e.priority),
                     'age': round(now - e.at, 1)}
                    for e in running
                ],
                'waiting': [
                    {'label': e.label, 'priority': int(e.priority)}
                    for e in sorted(lane.waiting, key=lambda e: (e.priority, e.seq))
                ],
            }
    out['paused'] = is_paused()
    out['counters'] = counters()
    return out


def reset() -> None:
    """Drop all state. For tests only."""
    global _next_seq
    with _cond:
        for lane in _lanes.values():
            lane.running.clear()
            lane.waiting.clear()
            lane.released_at = 0.0
        _next_seq = 1
        _cond.notify_all()
    with _events_lock:
        _events.clear()
        for key in _counters:
            _counters[key] = 0
    invalidate_pause_cache()
