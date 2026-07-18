"""Unit tests for the FSRS scheduling adapter (real fsrs library, no DB)."""
import json
import time
from datetime import datetime, timezone

import pytest

pytest.importorskip('fsrs')

from backend.learning import scheduler

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


def test_first_review_good_advances_due():
    state, due, log = scheduler.review(None, 3, now=NOW)
    assert due > int(NOW.timestamp())
    parsed = json.loads(state)
    assert parsed['stability'] is not None
    assert json.loads(log)['rating'] == 3


def test_again_due_sooner_than_easy():
    _, due_again, _ = scheduler.review(None, 1, now=NOW)
    _, due_easy, _ = scheduler.review(None, 4, now=NOW)
    assert due_again < due_easy


def test_state_json_roundtrip_across_reviews():
    state1, due1, _ = scheduler.review(None, 3, now=NOW)
    state2, due2, _ = scheduler.review(state1, 3)
    assert due2 > due1
    assert json.loads(state2)['stability'] is not None


def test_invalid_rating_rejected():
    with pytest.raises(ValueError):
        scheduler.review(None, 5, now=NOW)
    with pytest.raises(ValueError):
        scheduler.review(None, 0, now=NOW)


def test_stability_helper():
    assert scheduler.stability(None) is None
    state, _, _ = scheduler.review(None, 3, now=NOW)
    assert scheduler.stability(state) == json.loads(state)['stability']


def test_defaults_to_current_time():
    _, due, _ = scheduler.review(None, 1)
    # Again on a new card lands within the short learning steps (minutes).
    assert abs(due - time.time()) < 3600
