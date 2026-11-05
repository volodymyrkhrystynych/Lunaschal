"""Stars for a Health workout come from its heart rate, else its energy, and
never replace a rating given by hand."""
import time
import uuid

import pytest

from backend.apple_health import ingest, intensity
from backend.apple_health.lifestyle import RERATE_WINDOW_S, rate_workouts
from backend.db.connection import get_db

HR = intensity.HEART_RATE


def _workout(start, minutes=30, energy=None):
    item = dict(uuid=str(uuid.uuid4()), activityType=37, activityName='running',
                start=start, end=start + minutes * 60, duration=minutes * 60,
                metadata={'HKIndoorWorkout': False})
    if energy is not None:
        item['energy'] = energy
    return item


def _beats(*pairs):
    return [dict(uuid=str(uuid.uuid4()), type=HR, kind='quantity', value=bpm,
                 unit='count/min', start=at, end=at) for at, bpm in pairs]


def _session(client):
    sessions = client.get('/api/lifestyle/workouts').get_json()
    assert len(sessions) == 1
    return sessions[0]


@pytest.mark.parametrize('average, stars', [(90, 1), (110, 2), (130, 3), (150, 4), (180, 5), (250, 5)])
def test_heart_rate_bands_are_shares_of_the_peak(average, stars):
    assert intensity.from_heart_rate(average, 200) == stars


@pytest.mark.parametrize('kcal, stars', [(60, 1), (100, 2), (200, 3), (300, 4), (400, 5)])
def test_energy_bands_are_kcal_per_minute(kcal, stars):
    assert intensity.from_energy(kcal, 30) == stars


def test_nothing_to_go_on_is_no_rating():
    assert intensity.from_heart_rate(0, 180) is None
    assert intensity.from_energy(None, 30) is None
    assert intensity.from_energy(200, 0) is None


def test_heart_rate_during_the_workout_sets_the_stars(client):
    start = time.time() - 3600
    # An older hard effort sets the peak at 190.
    peak = _beats((start - 30 * 86400, 190))
    during = _beats((start + 60, 150), (start + 600, 156), (start + 1200, 162))
    ingest.ingest({'samples': peak + during, 'workouts': [_workout(start, energy=60)]})
    got = _session(client)
    # 156 / 190 = 82%: four, though 2 kcal/min alone would say one.
    assert got['intensityRating'] == 4
    assert got['intensitySource'] == 'health'


def test_a_peak_older_than_the_window_does_not_count(client):
    start = time.time() - 3600
    stale = _beats((start - intensity.PEAK_WINDOW_S - 86400, 220))
    during = _beats((start + 60, 120), (start + 600, 125), (start + 1200, 130))
    ingest.ingest({'samples': stale + during, 'workouts': [_workout(start)]})
    # The peak is the workout's own 130, not the 220 from last year.
    assert _session(client)['intensityRating'] == 5


def test_too_few_heart_rate_samples_falls_back_to_energy(client):
    start = time.time() - 3600
    ingest.ingest({'samples': _beats((start + 60, 170)),
                   'workouts': [_workout(start, energy=200)]})
    assert _session(client)['intensityRating'] == 3


def test_neither_heart_rate_nor_energy_leaves_it_unrated(client):
    ingest.ingest({'workouts': [_workout(time.time() - 3600)]})
    got = _session(client)
    assert got['intensityRating'] is None
    assert got['intensitySource'] is None


def test_heart_rate_that_arrives_later_rerates_a_recent_workout(client):
    start = time.time() - 3600
    w = _workout(start, energy=60)
    ingest.ingest({'workouts': [w]})
    assert _session(client)['intensityRating'] == 1
    ingest.ingest({'samples': _beats((start + 60, 160), (start + 600, 165), (start + 1200, 170))})
    got = _session(client)
    assert got['intensityRating'] == 5
    assert got['intensitySource'] == 'health'


def test_a_rating_by_hand_is_never_overwritten(client):
    start = time.time() - 3600
    ingest.ingest({'workouts': [_workout(start, energy=400)]})
    sid = _session(client)['id']
    assert client.patch(f'/api/lifestyle/workouts/{sid}', json={'intensityRating': 2}).status_code == 200
    ingest.ingest({'samples': _beats((start + 60, 180), (start + 600, 182), (start + 1200, 185))})
    got = _session(client)
    assert (got['intensityRating'], got['intensitySource']) == (2, 'user')
    # Cleared by hand stays cleared.
    assert client.patch(f'/api/lifestyle/workouts/{sid}', json={'intensityRating': None}).status_code == 200
    rate_workouts(get_db())
    assert _session(client)['intensityRating'] is None


def test_a_rating_from_before_the_source_column_is_left_alone(client):
    start = time.time() - 3600
    ingest.ingest({'workouts': [_workout(start)]})
    sid = _session(client)['id']
    db = get_db()
    db.execute('UPDATE workout_sessions SET intensity_rating=3, intensity_source=NULL WHERE id=?', (sid,))
    db.commit()
    ingest.ingest({'samples': _beats((start + 60, 180), (start + 600, 182), (start + 1200, 185))})
    assert _session(client)['intensityRating'] == 3


def test_an_old_derived_rating_is_not_rerated(client):
    start = time.time() - RERATE_WINDOW_S - 86400
    ingest.ingest({'workouts': [_workout(start, energy=60)]})
    assert _session(client)['intensityRating'] == 1
    ingest.ingest({'samples': _beats((start + 60, 170), (start + 600, 172), (start + 1200, 175))})
    assert _session(client)['intensityRating'] == 1
