"""The activity types and the per-day heatmap collapse (backend/lifestyle/activity.py)."""
import pytest

from backend.lifestyle.activity import (
    ACTIVITY_LABELS,
    ACTIVITY_TYPES,
    is_activity_type,
    priority,
    summarize_day,
)


def _session(location_type, **over):
    return {
        'locationType': location_type,
        'durationMinutes': None,
        'intensityRating': None,
        **over,
    }


def test_every_type_has_a_label_and_no_label_is_orphaned():
    assert set(ACTIVITY_LABELS) == set(ACTIVITY_TYPES)
    assert all(ACTIVITY_LABELS[t].strip() for t in ACTIVITY_TYPES)


def test_lifting_at_home_sits_between_building_and_outside():
    assert priority('building') < priority('lifting_home') < priority('outside')


def test_unknown_types_sort_last_rather_than_raising():
    """A row written by an older build must never break the heatmap."""
    assert priority('sky_diving') == len(ACTIVITY_TYPES)
    assert is_activity_type('lifting_home')
    assert not is_activity_type('sky diving')


@pytest.mark.parametrize('other', ['outside'])
def test_a_mixed_day_takes_lifting_at_home_over_lower_priority_activities(other):
    day = summarize_day([_session(other), _session('lifting_home')])
    assert day['activityType'] == 'lifting_home'
    assert day['secondary'] is True


def test_a_mixed_day_takes_the_gym_over_lifting_at_home():
    day = summarize_day([_session('lifting_home'), _session('goodlife_alone')])
    assert day['activityType'] == 'goodlife_alone'
    assert day['secondary'] is True


def test_two_home_sessions_are_one_activity_not_two():
    day = summarize_day([
        _session('lifting_home', durationMinutes=30, intensityRating=2),
        _session('lifting_home', durationMinutes=20, intensityRating=4),
    ])
    assert day['activityType'] == 'lifting_home'
    assert day['secondary'] is False
    assert day['durationMinutes'] == 50   # summed across the day
    assert day['intensityRating'] == 4    # the day's hardest session
