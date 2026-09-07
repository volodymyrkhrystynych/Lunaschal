"""Cancel-and-requeue: an interactive call takes the lane from a background one.

The point of preemption here is not politeness, it is that closing the HTTP
stream makes llama-server cancel the task and free the slot — so throwing the
partial generation away actually buys the GPU back. These tests cover our half
of that: the cancel event fires, the caller unwinds, and the lane is handed on.
"""
import threading
import time

import pytest

from backend.ai import service
from backend.ai.service import GPU, Priority

from backend.tests.test_llm_service import Holder, _wait_until


@pytest.fixture(autouse=True)
def clean_service():
    service.reset()
    yield
    service.reset()


def test_an_interactive_call_preempts_a_running_background_one():
    admitted = []
    bg = [Holder(f'bg{i}', admitted, priority=Priority.BACKGROUND).start().wait_in()
          for i in range(2)]

    fg = Holder('interactive', admitted).start()
    fg.wait_in()          # gets in without anyone releasing anything
    fg.finish()

    for b in bg:
        b.thread.join(2.0)
        assert b.cancelled, f'{b.name} was not cancelled'


def test_a_non_preemptible_background_call_is_left_alone():
    """The escape hatch for a job cancelled so often it would never finish."""
    admitted = []
    stubborn = Holder('stubborn', admitted, priority=Priority.BACKGROUND,
                      preemptible=False).start().wait_in()

    fg = Holder('interactive', admitted).start().wait_in()
    time.sleep(0.05)
    assert not stubborn.cancelled

    fg.finish()
    stubborn.finish()
    assert not stubborn.cancelled


def test_an_interactive_call_takes_the_free_slot_rather_than_waiting():
    """One background call in flight must not delay a user by a whole generation."""
    admitted = []
    bg = Holder('background', admitted, priority=Priority.BACKGROUND,
                preemptible=False).start().wait_in()
    # One slot is busy with work we've promised not to cancel; the user should
    # still start immediately in the other rather than queue behind it.
    fg = Holder('interactive', admitted).start()
    fg.wait_in()
    fg.finish()
    bg.finish()


def test_background_work_resumes_once_the_user_stops():
    admitted = []
    fg = Holder('interactive', admitted).start().wait_in()
    bg = Holder('background', admitted, priority=Priority.BACKGROUND).start()
    assert _wait_until(lambda: service.status()[GPU]['waiting'])
    fg.finish()
    bg.wait_in()
    bg.finish()
    assert admitted == ['interactive', 'background']


def test_the_stream_iterator_raises_preempted_and_closes_the_response():
    """`_iter_stream` is what turns a set cancel event into a freed slot."""
    from backend.ai.llm import _iter_stream

    class FakeStream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            for i in range(100):
                yield i

        def close(self):
            self.closed = True

    stream = FakeStream()
    cancel = threading.Event()
    seen = []
    with pytest.raises(service.Preempted):
        for chunk in _iter_stream(stream, cancel):
            seen.append(chunk)
            if len(seen) == 3:
                cancel.set()

    assert seen == [0, 1, 2]
    assert stream.closed, 'the response must be closed, or the slot stays busy'


def test_the_stream_is_closed_even_when_the_consumer_raises():
    from backend.ai.llm import _iter_stream

    class FakeStream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield 1
            yield 2

        def close(self):
            self.closed = True

    stream = FakeStream()
    with pytest.raises(RuntimeError):
        for _ in _iter_stream(stream, threading.Event()):
            raise RuntimeError('caller exploded')
    assert stream.closed


def test_idle_seconds_tracks_the_last_interactive_call():
    assert service.idle_seconds(GPU) == float('inf')
    admitted = []
    fg = Holder('interactive', admitted).start().wait_in()
    assert service.idle_seconds(GPU) == 0.0
    assert service.interactive_active(GPU) is True
    fg.finish()
    assert service.idle_seconds(GPU) < 1.0
    assert service.interactive_active(GPU) is False


def test_a_queued_interactive_call_counts_as_active():
    """A scheduler must not read 'nothing running' while a user waits in line."""
    admitted = []
    blockers = [Holder(f'b{i}', admitted).start().wait_in() for i in range(2)]
    waiter = Holder('queued', admitted).start()
    assert _wait_until(lambda: service.status()[GPU]['waiting'])
    assert service.interactive_active(GPU) is True
    for b in blockers:
        b.finish()
    waiter.wait_in()
    waiter.finish()


def test_a_waiter_that_gives_up_does_not_block_the_lane_forever():
    """`waiting` has no TTL sweep the way `running` does.

    An entry abandoned at the head of the queue would be checked by every other
    waiter, chosen every time, and never taken — so the lane would be dead for
    the life of the process. This is the one place a leak is unrecoverable.
    """
    import contextlib

    admitted = []
    blockers = [Holder(f'b{i}', admitted).start().wait_in() for i in range(2)]

    # A waiter that raises out of its wait, the way a KeyboardInterrupt or a
    # killed test thread would.
    def abandons():
        with contextlib.suppress(RuntimeError):
            with service.slot(lane=GPU, label='gives-up'):
                pass

    original_wait = service._cond.wait

    def wait_then_explode(timeout=None):
        service._cond.wait = original_wait
        raise RuntimeError('abandoned')

    service._cond.wait = wait_then_explode
    try:
        abandons()
    finally:
        service._cond.wait = original_wait

    assert service.status()[GPU]['waiting'] == []

    for b in blockers:
        b.finish()
    # The lane still works.
    later = Holder('after', admitted).start()
    later.wait_in()
    later.finish()
