"""The assistant's own note queue (backend/observations.py).

The chat delegate's `remember` tool writes here rather than into the user's
memory document. Both caps in this module are deliberate rather than
housekeeping: these ride in every chat system prompt, twice per turn.
"""
import time

import pytest

from backend import observations
from backend.db.connection import get_db


def test_an_observation_is_normalised_before_it_is_stored(client):
    observations.add_observation('  Trains   on\n  Tuesdays  ')
    assert [o['content'] for o in observations.pending()] == ['Trains on Tuesdays']


def test_an_empty_observation_writes_nothing(client):
    assert observations.add_observation('   ') is None
    assert observations.pending() == []


def test_a_duplicate_is_dropped_rather_than_stored_twice(client):
    """The model can see only the most recent slice of its own notes, so
    re-stating something it already saved is normal."""
    observations.add_observation('Trains on Tuesdays')
    assert observations.add_observation('trains ON tuesdays') is None
    assert len(observations.pending()) == 1


def test_an_observation_longer_than_a_standing_fact_is_refused(client):
    with pytest.raises(ValueError):
        observations.add_observation('x' * (observations.MAX_CHARS + 1))


def test_a_full_queue_refuses_rather_than_dropping_the_oldest(client):
    """Trimming silently would make the assistant the one deciding what stops
    mattering about the user. `set_memory` takes the same stance at its cap."""
    for i in range(observations.MAX_PENDING):
        observations.add_observation(f'Fact number {i}')

    with pytest.raises(observations.ObservationsFull):
        observations.add_observation('One more thing')

    assert len(observations.pending()) == observations.MAX_PENDING


def test_a_folded_observation_stops_counting_against_the_cap(client):
    """Folding is what the nightly synthesis pass does. Until it exists nothing
    sets folded_at, but the queue must already drain correctly when it does."""
    db = get_db()
    observations.add_observation('Trains on Tuesdays')
    db.execute('UPDATE assistant_observations SET folded_at=?', (int(time.time()),))
    db.commit()

    assert observations.pending() == []
    assert observations.pending_count() == 0
    # And it is no longer a duplicate, so a fact that resurfaces can be renoted.
    assert observations.add_observation('Trains on Tuesdays') is not None


def test_the_prompt_block_is_capped_below_the_queue(client):
    for i in range(observations.MAX_PENDING):
        observations.add_observation(f'Fact number {i}')

    block = observations.format_observations_context()
    assert block.count('- Fact number') == observations.PROMPT_LIMIT


def test_the_prompt_block_is_empty_when_nothing_is_pending(client):
    assert observations.format_observations_context() == ''


def test_the_newest_notes_are_the_ones_that_reach_the_prompt(client):
    for i in range(observations.PROMPT_LIMIT + 3):
        observations.add_observation(f'Fact number {i}', now=1_700_000_000 + i)

    block = observations.format_observations_context()
    assert f'Fact number {observations.PROMPT_LIMIT + 2}' in block
    assert 'Fact number 0' not in block


def test_deleting_one_removes_it(client):
    stored = observations.add_observation('Trains on Tuesdays')
    assert observations.delete_observation(stored['id']) is True
    assert observations.pending() == []
    assert observations.delete_observation(stored['id']) is False
