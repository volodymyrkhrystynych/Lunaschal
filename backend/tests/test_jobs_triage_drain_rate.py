"""The triage drain keeps going while the machine is idle.

`drain_once` submits one posting and returns — the right shape for
`queue.drain_once`, which it was copied from, because a tailoring pass is a
multi-minute generation. Under a 300-second poll that same shape gave triage a
ceiling of twelve postings an hour: four seconds of model, 296 of sleeping.

What is pinned here is that removing the sleep changed *only* the sleeping —
every deferral gate is still consulted between every generation.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from backend.jobs import scheduler, triager


@pytest.fixture(autouse=True)
def _quiet_priority():
    """No interactive call in flight unless a test says otherwise."""
    from backend.ai import priority
    priority.reset()
    yield
    priority.reset()


def _drain(sequence, **kwargs):
    """Run the loop with `drain_once` yielding `sequence`, and count calls."""
    with patch.object(triager, 'drain_once', side_effect=sequence) as once, \
         patch.object(triager, 'wait_idle', return_value=True):
        return triager.drain_while_idle(**kwargs), once


# --------------------------------------------------------------------------
# The loop itself
# --------------------------------------------------------------------------

def test_it_keeps_going_instead_of_stopping_after_one():
    result, once = _drain(['a', 'b', 'c', None])
    assert result == {'submitted': 3, 'stopped': 'idle'}
    assert once.call_count == 4


def test_an_empty_queue_returns_immediately():
    result, once = _drain([None])
    assert result == {'submitted': 0, 'stopped': 'idle'}
    assert once.call_count == 1


def test_it_stops_when_the_budget_runs_out():
    """One tick must not run for hours with linkage and sync behind it."""
    result, once = _drain(['a'] * 50, budget_seconds=0)
    # Budget 0 is the documented way back to one-per-tick exactly.
    assert result == {'submitted': 1, 'stopped': 'budget'}
    assert once.call_count == 1


def test_every_generation_is_waited_on_before_the_next_is_considered():
    """Otherwise the executor queues them and no gate is read in between.

    That is the whole reason this loops over `drain_once` rather than raising
    a batch size inside it.
    """
    calls = []
    with patch.object(triager, 'drain_once',
                      side_effect=lambda: calls.append('submit') or 'id') as once, \
         patch.object(triager, 'wait_idle',
                      side_effect=lambda **kw: calls.append('wait') or True):
        triager.drain_while_idle(budget_seconds=0.05)

    assert once.call_count >= 1
    # Never two submissions without a wait between them.
    for earlier, later in zip(calls, calls[1:]):
        assert not (earlier == 'submit' and later == 'submit')


def test_a_wedged_generation_releases_the_tick():
    """The row stays pending and is retried — what would have happened anyway."""
    with patch.object(triager, 'drain_once', return_value='a'), \
         patch.object(triager, 'wait_idle', return_value=False):
        assert triager.drain_while_idle() == {'submitted': 1, 'stopped': 'timeout'}


# --------------------------------------------------------------------------
# The deferral behaviour is unchanged — it is only read more often
# --------------------------------------------------------------------------

def test_an_interactive_call_stands_the_drain_down_between_generations(client):
    """A chat message still parks the drain, now checked per generation."""
    from backend.ai import priority

    client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme',
                                   'description': 'python'})
    token = priority.begin('chat.stream')
    try:
        assert triager.drain_while_idle() == {'submitted': 0, 'stopped': 'idle'}
    finally:
        priority.end(token)


def test_the_drain_stops_mid_loop_when_the_user_comes_back():
    """Two postings in, `drain_once` declines and the loop ends there."""
    result, once = _drain(['a', 'b', None, 'c'])
    assert result['submitted'] == 2
    assert result['stopped'] == 'idle'
    # 'c' is never reached: the loop broke on the None rather than skipping it.
    assert once.call_count == 3


def test_a_paused_scheduler_never_reaches_the_drain(client):
    from backend.db.connection import get_db

    db = get_db()
    db.execute('UPDATE settings SET jobs_paused=1')
    db.commit()

    with patch.object(triager, 'drain_while_idle') as drain, \
         patch.object(scheduler.linker, 'run_linkage_sweep', return_value={}):
        scheduler.tick(now=datetime(2026, 8, 30, 12, 0))
    drain.assert_not_called()


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------

def test_the_resume_queue_runs_before_the_triage_drain(client):
    """A resume was asked for by tapping Queue; triage is speculative.

    Now that the drain can hold the tick for minutes, making the explicit
    request wait behind the speculative one is the wrong way round.
    """
    order = []
    with patch.object(scheduler.queue, 'drain_once',
                      side_effect=lambda: order.append('queue')), \
         patch.object(triager, 'drain_while_idle',
                      side_effect=lambda: order.append('triage')), \
         patch.object(scheduler.sync, 'run_sync_sweep', return_value={}), \
         patch.object(scheduler.career_watch, 'run_due'), \
         patch.object(scheduler.workday_watch, 'run_due'), \
         patch.object(scheduler.triager, 'run_gate_sweep'), \
         patch.object(scheduler.linker, 'run_linkage_sweep', return_value={}):
        scheduler.tick(now=datetime(2026, 8, 30, 12, 0))

    assert order == ['queue', 'triage']


def test_a_real_drain_walks_the_whole_backlog_one_generation_at_a_time(client):
    """End to end through the real submit/wait path, with the model stubbed.

    This is the one that would catch a race: `submit` sets `_current` under the
    lock *before* handing the work to the executor, so `wait_idle` after a
    successful `drain_once` genuinely waits for that generation rather than
    returning while the worker is still starting. If it did not, the loop would
    stack every posting onto the executor and no priority check would happen
    between them.
    """
    from backend.db.connection import get_db

    judged = []

    def fake_process(job_id):
        judged.append(job_id)
        # A real verdict moves the row out of `pending`; the stub has to as
        # well, or the drain is being asked to make progress it cannot see.
        db = get_db()
        db.execute("UPDATE jobs SET triage_state='kept' WHERE id=?", (job_id,))
        db.commit()
        return {'ok': True, 'state': 'kept', 'error': None}

    for n in range(5):
        client.post('/api/jobs', json={'title': f'Engineer {n}',
                                       'company': 'Acme',
                                       'description': 'python and sql'})

    with patch.object(triager, 'process_one', side_effect=fake_process):
        result = triager.drain_while_idle(budget_seconds=30)

    assert result['submitted'] == 5
    assert result['stopped'] == 'idle'
    assert len(judged) == 5
    assert len(set(judged)) == 5      # each posting judged exactly once


def test_a_model_that_never_answers_stops_the_loop_instead_of_spinning(client):
    """`process_one` leaves a row `pending` when llama-server is unreachable.

    That is deliberate — a verdict nobody reached must not be recorded — but it
    means the row comes straight back as the next candidate. At one posting per
    five minutes that was invisible; a loop turns it into thousands of retries
    against a dead endpoint inside a single tick.
    """
    attempts = []

    def never_answers(job_id):
        attempts.append(job_id)
        return {'ok': False, 'state': 'pending', 'error': 'AI unavailable'}

    client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme',
                                   'description': 'python'})

    with patch.object(triager, 'process_one', side_effect=never_answers):
        result = triager.drain_while_idle(budget_seconds=30)

    assert result == {'submitted': 1, 'stopped': 'stalled'}
    # One attempt, not thousands — and the repeat is spotted by looking ahead,
    # so it is never handed to the worker at all.
    assert attempts == [attempts[0]]
