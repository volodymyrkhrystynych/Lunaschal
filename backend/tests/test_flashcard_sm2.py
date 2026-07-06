"""Unit tests for the SM-2 spaced-repetition step (`backend.routes.flashcard._sm2`).

`_sm2` is a pure function — no DB, no Flask context — so it's a good first
target for the backend test suite. It returns `(new_interval, new_reps, new_ef)`
given the card's current `(interval, repetitions, efactor)` and the review
`grade` (0-5, where >=3 is a "pass").
"""
import pytest

from backend.routes.flashcard import _sm2


def test_first_successful_review_sets_interval_to_one_day():
    interval, reps, ef = _sm2(interval=0, repetitions=0, efactor=2.5, grade=4)
    assert interval == 1
    assert reps == 1


def test_second_successful_review_sets_interval_to_six_days():
    interval, reps, ef = _sm2(interval=1, repetitions=1, efactor=2.5, grade=4)
    assert interval == 6
    assert reps == 2


def test_third_successful_review_multiplies_interval_by_efactor():
    interval, reps, ef = _sm2(interval=6, repetitions=2, efactor=2.5, grade=4)
    # round(6 * 2.5) == 15
    assert interval == 15
    assert reps == 3


def test_failing_grade_resets_interval_and_repetitions():
    interval, reps, ef = _sm2(interval=15, repetitions=3, efactor=2.5, grade=1)
    assert interval == 1
    assert reps == 0


def test_efactor_never_drops_below_floor():
    # Repeatedly failing must clamp the easiness factor at 1.3.
    ef = 2.5
    interval, reps = 0, 0
    for _ in range(20):
        interval, reps, ef = _sm2(interval, reps, ef, grade=0)
    assert ef == pytest.approx(1.3)


def test_perfect_grade_increases_efactor():
    _, _, ef = _sm2(interval=6, repetitions=2, efactor=2.5, grade=5)
    # +0.1 for a grade-5 review
    assert ef == pytest.approx(2.6)
