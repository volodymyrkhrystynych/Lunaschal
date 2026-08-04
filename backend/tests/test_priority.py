"""The interactive-first gate.

Pure unit tests plus one that drives a stream_with_context-shaped generator,
because the client-disconnect path is the part that must not regress.
"""
import threading
import time

import pytest

from backend.ai import priority


@pytest.fixture(autouse=True)
def clean_gate():
    priority.reset()
    yield
    priority.reset()


def test_begin_and_end_track_activity():
    assert not priority.active()
    token = priority.begin('chat')
    assert priority.active()
    priority.end(token)
    assert not priority.active()


def test_marks_nest():
    a = priority.begin('chat')
    b = priority.begin('classify')
    priority.end(a)
    assert priority.active(), 'still busy while the second call is in flight'
    priority.end(b)
    assert not priority.active()


def test_end_is_idempotent_and_tolerates_unknown_tokens():
    token = priority.begin('chat')
    priority.end(token)
    priority.end(token)  # must not raise
    priority.end(999999)
    assert not priority.active()


def test_context_manager_releases_on_exception():
    with pytest.raises(RuntimeError):
        with priority.interactive('boom'):
            raise RuntimeError('generation failed')
    assert not priority.active()


def test_an_expired_mark_stops_counting(monkeypatch):
    """A leaked mark must not starve background work forever."""
    monkeypatch.setattr(priority, 'MARK_TTL', 0.05)
    priority.begin('leaked')
    assert priority.active()
    time.sleep(0.06)
    assert not priority.active()


def test_snapshot_reports_in_flight_labels():
    priority.begin('chat.stream')
    labels = [m['label'] for m in priority.snapshot()]
    assert labels == ['chat.stream']


def test_wait_for_idle_returns_immediately_when_quiet():
    assert priority.wait_for_idle(timeout=1, grace=0) is True


def test_wait_for_idle_blocks_until_the_call_finishes():
    token = priority.begin('chat')
    released = threading.Event()

    def finish():
        time.sleep(0.05)
        priority.end(token)
        released.set()

    threading.Thread(target=finish, daemon=True).start()
    assert priority.wait_for_idle(timeout=5, grace=0) is True
    assert released.is_set()


def test_wait_for_idle_honours_the_grace_period():
    priority.end(priority.begin('chat'))
    started = time.monotonic()
    assert priority.wait_for_idle(timeout=5, grace=0.1) is True
    assert time.monotonic() - started >= 0.09


def test_wait_for_idle_times_out_and_the_caller_proceeds():
    """False means "go anyway" — deferring research forever is the worse bug."""
    priority.begin('stuck')
    started = time.monotonic()
    assert priority.wait_for_idle(timeout=0.1, grace=0) is False
    assert time.monotonic() - started < 2


def test_wait_for_idle_returns_false_when_cancelled():
    priority.begin('stuck')
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    assert priority.wait_for_idle(timeout=5, grace=0, cancel=cancel) is False


# --- The streaming path ---

def test_generator_releases_the_mark_when_the_client_disconnects():
    """The shape backend/routes/chat.py uses. Werkzeug stops iterating on a
    client disconnect and drops its reference; CPython closes the generator,
    raising GeneratorExit at the suspended yield, and `finally` runs.

    If the mark were acquired inside the generator instead of in the view, the
    window before the first token would look idle; if it were released outside
    the generator, a disconnect would leak it permanently.
    """
    token = priority.begin('chat.stream')

    def generate():
        try:
            for i in range(1000):
                yield f'chunk {i}'
        finally:
            priority.end(token)

    stream = generate()
    next(stream)
    assert priority.active(), 'busy while streaming'

    stream.close()  # what a client disconnect amounts to
    assert not priority.active()


def test_generator_releases_the_mark_on_normal_completion():
    token = priority.begin('chat.stream')

    def generate():
        try:
            yield 'only chunk'
        finally:
            priority.end(token)

    assert list(generate()) == ['only chunk']
    assert not priority.active()


def test_mark_is_held_from_before_the_first_chunk():
    """Acquiring in the view, not the generator body, is what covers the
    time-to-first-token — which at 25 tok/s is most of the wait."""
    token = priority.begin('chat.stream')

    def generate():
        try:
            yield 'x'
        finally:
            priority.end(token)

    stream = generate()
    # Generator not started yet, but the user is already waiting.
    assert priority.active()
    stream.close()


# --- run_bg wiring ---

def test_run_bg_marks_its_work_as_interactive():
    """Deferred work was triggered by a user action seconds ago, so research
    must yield to it too."""
    from backend.ai.background import run_bg

    seen = threading.Event()
    was_active = []

    def work():
        was_active.append(priority.active())
        seen.set()

    run_bg(work)
    assert seen.wait(5)
    assert was_active == [True]
    # And the mark is released once the work finishes.
    for _ in range(50):
        if not priority.active():
            break
        time.sleep(0.02)
    assert not priority.active()


def test_run_bg_releases_the_mark_when_the_work_raises():
    from backend.ai.background import run_bg

    done = threading.Event()

    def work():
        try:
            raise RuntimeError('classification failed')
        finally:
            done.set()

    run_bg(work)
    assert done.wait(5)
    for _ in range(50):
        if not priority.active():
            break
        time.sleep(0.02)
    assert not priority.active()
