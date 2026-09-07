"""Admission, ordering and lanes in backend/ai/service.py.

Everything here drives `slot()` directly with real threads: the ordering rules
are about what happens when several callers contend, and a test that never
contends cannot see them. Synchronisation is by `threading.Event` rather than
sleeps — a timing-based test of a scheduler is a flaky test of a scheduler.
"""
import threading
import time

import pytest

from backend.ai import service
from backend.ai.service import GPU, CPU, Priority


@pytest.fixture(autouse=True)
def clean_service():
    service.reset()
    yield
    service.reset()


class Holder:
    """A thread that takes a slot, records that it got in, and waits to be let go."""

    def __init__(self, name, admitted, *, lane=GPU, priority=Priority.INTERACTIVE,
                 preemptible=True):
        self.name = name
        self.admitted = admitted          # shared list, in admission order
        self.lane = lane
        self.priority = priority
        self.preemptible = preemptible
        self.release = threading.Event()
        self.got_in = threading.Event()
        self.cancelled = False
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with service.slot(lane=self.lane, label=self.name,
                              priority=self.priority,
                              preemptible=self.preemptible) as cancel:
                self.admitted.append(self.name)
                self.got_in.set()
                while not self.release.wait(0.01):
                    if cancel.is_set():
                        self.cancelled = True
                        return
        except Exception as e:  # InferencePaused et al.
            self.error = e
            self.got_in.set()

    def start(self):
        self.thread.start()
        return self

    def wait_in(self, timeout=2.0):
        assert self.got_in.wait(timeout), f'{self.name} was never admitted'
        return self

    def finish(self, timeout=2.0):
        self.release.set()
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f'{self.name} did not exit'


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_interactive_is_admitted_before_background_whatever_the_order():
    admitted = []
    # Fill both GPU slots so nothing new can start.
    blockers = [Holder(f'block{i}', admitted).start().wait_in() for i in range(2)]

    # Background queues first, interactive second — priority must still win.
    bg = Holder('background', admitted, priority=Priority.BACKGROUND).start()
    assert _wait_until(lambda: any(
        e['label'] == 'background' for e in service.status()[GPU]['waiting']))
    fg = Holder('interactive', admitted).start()
    assert _wait_until(lambda: any(
        e['label'] == 'interactive' for e in service.status()[GPU]['waiting']))

    for b in blockers:
        b.finish()
    fg.wait_in()
    fg.finish()
    bg.wait_in()
    bg.finish()

    assert admitted[2:] == ['interactive', 'background']


def test_same_priority_is_fifo():
    admitted = []
    blockers = [Holder(f'block{i}', admitted).start().wait_in() for i in range(2)]

    waiters = []
    for name in ('first', 'second', 'third'):
        w = Holder(name, admitted).start()
        # Enqueue one at a time so submission order is unambiguous.
        assert _wait_until(lambda n=name: any(
            e['label'] == n for e in service.status()[GPU]['waiting']))
        waiters.append(w)

    for b in blockers:
        b.finish()
    for w in waiters:
        w.wait_in()
        w.finish()

    assert admitted[2:] == ['first', 'second', 'third']


def test_two_interactive_calls_share_the_lane():
    """`parallel = 2` on [qwen36] exists so a second call need not wait."""
    admitted = []
    a = Holder('a', admitted).start().wait_in()
    b = Holder('b', admitted).start().wait_in()
    assert len(service.status()[GPU]['running']) == 2
    a.finish()
    b.finish()


def test_background_does_not_run_beside_an_interactive_call():
    """The second slot is for a second *user* call, not for background work."""
    admitted = []
    fg = Holder('interactive', admitted).start().wait_in()
    bg = Holder('background', admitted, priority=Priority.BACKGROUND).start()

    assert _wait_until(lambda: any(
        e['label'] == 'background' for e in service.status()[GPU]['waiting']))
    # Still waiting: one interactive call in flight is enough to hold it off.
    time.sleep(0.05)
    assert not bg.got_in.is_set()

    fg.finish()
    bg.wait_in()
    bg.finish()


def test_the_lanes_are_independent():
    """A photo caption must not queue behind a nightly briefing."""
    admitted = []
    gpu = [Holder(f'gpu{i}', admitted, lane=GPU).start().wait_in() for i in range(2)]
    # GPU lane is full; the CPU lane should not care in the slightest.
    cpu = Holder('cpu', admitted, lane=CPU).start().wait_in()
    cpu.finish()
    for g in gpu:
        g.finish()


def test_a_leaked_slot_stops_counting_and_cannot_starve_background_work(monkeypatch):
    """The failure this guards against is 'background work never runs again'."""
    monkeypatch.setattr(service, 'ENTRY_TTL', 0.05)
    admitted = []
    leaked = [Holder(f'leak{i}', admitted).start().wait_in() for i in range(2)]

    bg = Holder('background', admitted, priority=Priority.BACKGROUND).start()
    assert _wait_until(lambda: bg.got_in.is_set(), timeout=3.0), \
        'background work was starved by leaked slots'
    bg.finish()
    for holder in leaked:
        holder.finish()


def test_status_reports_what_is_running_and_waiting():
    admitted = []
    holders = [Holder(f'h{i}', admitted).start().wait_in() for i in range(2)]
    waiter = Holder('waiting-one', admitted).start()
    assert _wait_until(lambda: service.status()[GPU]['waiting'])

    snap = service.status()
    assert {e['label'] for e in snap[GPU]['running']} == {'h0', 'h1'}
    assert [e['label'] for e in snap[GPU]['waiting']] == ['waiting-one']
    assert snap['paused'] is False

    for h in holders:
        h.finish()
    waiter.wait_in()
    waiter.finish()


def test_the_slot_is_released_when_the_body_raises():
    def boom():
        with service.slot(lane=GPU, label='explodes'):
            raise RuntimeError('nope')

    with pytest.raises(RuntimeError):
        boom()
    assert service.status()[GPU]['running'] == []


def test_a_generator_releases_its_slot_when_the_consumer_walks_away():
    """An SSE client that disconnects must not hold the lane until the TTL."""
    def gen():
        with service.slot(lane=GPU, label='stream'):
            yield 1
            yield 2

    g = gen()
    next(g)
    assert len(service.status()[GPU]['running']) == 1
    g.close()          # what GeneratorExit looks like from the outside
    assert service.status()[GPU]['running'] == []


# ------------------------------------------------------- per-thread priority

def test_priority_defaults_to_interactive():
    assert service.current_priority() is Priority.INTERACTIVE


def test_background_marks_the_thread_and_restores_it():
    with service.background():
        assert service.current_priority() is Priority.BACKGROUND
    assert service.current_priority() is Priority.INTERACTIVE


def test_nested_marks_restore_the_outer_value_not_the_default():
    """A background job calling a helper that also marks itself must stay P2."""
    with service.background():
        with service.background():
            assert service.current_priority() is Priority.BACKGROUND
        assert service.current_priority() is Priority.BACKGROUND
    assert service.current_priority() is Priority.INTERACTIVE


def test_interactive_can_be_reclaimed_inside_a_background_thread():
    with service.background():
        with service.interactive():
            assert service.current_priority() is Priority.INTERACTIVE
        assert service.current_priority() is Priority.BACKGROUND


def test_a_slot_inherits_the_threads_priority():
    admitted = []
    fg = Holder('interactive', admitted).start().wait_in()

    started = threading.Event()
    got_in = threading.Event()

    def worker():
        with service.background():
            started.set()
            with service.slot(lane=GPU, label='inherited'):
                got_in.set()

    threading.Thread(target=worker, daemon=True).start()
    assert started.wait(2.0)
    # If it had inherited INTERACTIVE it would have taken the free second slot.
    time.sleep(0.05)
    assert not got_in.is_set()
    fg.finish()
    assert got_in.wait(2.0)
