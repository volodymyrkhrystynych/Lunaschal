"""Health projections and sleep quality use isolated data, never real Health."""
import uuid
from datetime import datetime

import pytest

from backend.apple_health import ingest, nights
from backend.apple_health.lifestyle import BODY_MASS, KG_PER_LB
from backend.db.connection import get_db, _ensure_health_lifestyle


def at(day, hour):
    return datetime.fromisoformat(f'{day}T{hour}:00').timestamp()


def mass(value, day='2026-07-08', hour='09:00', unit='kg'):
    return dict(uuid=str(uuid.uuid4()), type=BODY_MASS, kind='quantity', value=value,
                unit=unit, start=at(day, hour), end=at(day, hour))


def workout():
    return dict(uuid=str(uuid.uuid4()), activityType=52, activityName='walking',
                start=at('2026-07-08', '02:00'), end=at('2026-07-08', '02:30'),
                duration=1800, metadata={'HKIndoorWorkout': False})


def weights(client):
    return client.get('/api/lifestyle/weight').get_json()


def test_workout_is_logged_once_with_original_day_and_no_invented_rating(client):
    w = workout()
    ingest.ingest({'workouts': [w]})
    first = client.get('/api/lifestyle/workouts').get_json()[0]
    assert first['date'] == '2026-07-07'
    assert first['captureKind'] == 'health'
    assert first['locationType'] == 'outside'
    assert first['intensityRating'] is None
    assert first['durationMinutes'] == 30
    assert first['parseStatus'] == 'skipped'
    assert first['exercises'] == []
    assert client.patch(f"/api/lifestyle/workouts/{first['id']}", json={'intensityRating': 4}).status_code == 200
    ingest.ingest({'workouts': [w]})
    _ensure_health_lifestyle(get_db())
    got = client.get('/api/lifestyle/workouts').get_json()
    assert len(got) == 1 and got[0]['id'] == first['id']
    assert got[0]['intensityRating'] == 4
    assert client.get('/api/lifestyle/heatmap').get_json()[0]['date'] == '2026-07-07'


def test_user_deleted_workout_is_not_resurrected_by_replay_or_restart(client):
    w = workout()
    ingest.ingest({'workouts': [w]})
    sid = client.get('/api/lifestyle/workouts').get_json()[0]['id']
    client.delete(f'/api/lifestyle/workouts/{sid}')
    ingest.ingest({'workouts': [w]})
    _ensure_health_lifestyle(get_db())
    assert client.get('/api/lifestyle/workouts').get_json() == []


def test_health_deletion_removes_import_only(client):
    w = workout()
    ingest.ingest({'workouts': [w]})
    manual = client.post('/api/lifestyle/workouts', json={'locationType': 'building'}).get_json()
    ingest.ingest({'deleted': [w['uuid']]})
    assert [r['id'] for r in client.get('/api/lifestyle/workouts').get_json()] == [manual['id']]


def test_projection_cannot_fail_a_batch_on_an_oversized_duration(client):
    w = {**workout(), 'duration': 1e100}
    assert ingest.ingest({'workouts': [w]})['workouts'] == 1
    assert client.get('/api/lifestyle/workouts').get_json()[0]['durationMinutes'] == 30


def test_backfill_handles_existing_mirror_and_normalizes_legacy_weight_once(client):
    db = get_db()
    # Emulate data uploaded before projection was implemented.
    w = workout()
    db.execute('INSERT INTO health_workouts(id,activity_type,activity_name,start_ts,end_ts,duration_s,received_at)'
               ' VALUES (?,?,?,?,?,?,?)', (w['uuid'], 52, 'walking', w['start'], w['end'], 1800, 1))
    db.execute('INSERT INTO body_weight_logs(id,date,weight,created_at,updated_at) VALUES (?,?,?,?,?)',
               ('old', '2026-07-01', 400, 1, 1))
    sample = mass(180)
    db.execute('INSERT INTO health_samples(id,type,kind,start_ts,end_ts,value,unit,received_at)'
               ' VALUES (?,?,?,?,?,?,?,?)',
               (sample['uuid'], BODY_MASS, 'quantity', sample['start'], sample['end'], 180, 'kg', 1))
    # An upgrade from a schema without either provenance/unit column.
    db.execute('ALTER TABLE body_weight_logs DROP COLUMN weight_unit')
    db.execute('ALTER TABLE body_weight_logs DROP COLUMN health_sample_id')
    _ensure_health_lifestyle(db)
    _ensure_health_lifestyle(db)
    assert weights(client)[0]['weight'] == pytest.approx(400 * KG_PER_LB)
    assert weights(client)[0]['weightUnit'] == 'kg'
    assert weights(client)[1]['weight'] == pytest.approx(180 * KG_PER_LB)
    assert len(client.get('/api/lifestyle/workouts').get_json()) == 1


@pytest.mark.parametrize('value,unit,expected', [(80, 'kg', 80), (180, 'kg', 180 * KG_PER_LB),
                                               (140, 'kg', 140 * KG_PER_LB), (139.9, 'kg', 139.9),
                                               (180, 'lb', 180 * KG_PER_LB), (80000, 'g', 80)])
def test_weight_cutoff_and_units(client, value, unit, expected):
    sample = mass(value, unit=unit)
    ingest.ingest({'samples': [sample]})
    ingest.ingest({'samples': [sample]})
    assert len(weights(client)) == 1
    assert weights(client)[0]['weight'] == pytest.approx(expected)
    assert get_db().execute('SELECT value FROM health_samples').fetchone()[0] == value


def test_latest_weight_wins_and_deleting_it_falls_back(client):
    older, newer = mass(80, hour='01:00'), mass(81, day='2026-07-07', hour='23:00')
    # 1am is later but still belongs to July 7.
    ingest.ingest({'samples': [older, newer]})
    assert [(r['date'], r['weight']) for r in weights(client)] == [('2026-07-07', 80)]
    ingest.ingest({'deleted': [older['uuid']]})
    assert weights(client)[0]['weight'] == 81
    ingest.ingest({'deleted': [newer['uuid']]})
    assert weights(client) == []


def test_manual_weight_correction_wins_and_explicit_kg_is_not_guessed(client):
    sample = mass(180)
    ingest.ingest({'samples': [sample]})
    client.post('/api/lifestyle/weight', json={'date': '2026-07-08', 'weight': 145, 'unit': 'kg'})
    ingest.ingest({'samples': [sample]})
    ingest.ingest({'deleted': [sample['uuid']]})
    assert weights(client)[0]['weight'] == 145
    _ensure_health_lifestyle(get_db())
    assert weights(client)[0]['weight'] == 145


def test_legacy_phone_weight_upload_uses_cutoff(client):
    client.post('/api/lifestyle/weight', json={'weight': 180})
    assert weights(client)[0]['weight'] == pytest.approx(180 * KG_PER_LB)
    assert client.post('/api/lifestyle/weight', json={'weight': 80, 'unit': 'stone'}).status_code == 400


def night_samples(gap=False):
    start, wake, resume, end = [at('2026-07-08', hour) for hour in ('00:00', '03:00', '03:15', '08:00')]
    samples = [(start, wake, 3), (resume, end, 5)]
    if not gap:
        samples += [(wake, resume, 2), (wake, resume, 2), (start, end, 1), (start, end, 0)]
    return samples


def test_sleep_unions_sources_and_awake_overrides_broad_asleep():
    result = nights.sleep_summary('2026-07-08', night_samples())
    assert result['asleepMinutes'] == 465
    assert result['awakeMinutes'] == 15
    assert result['interruptions'] == 1
    assert result['efficiency'] == 96.9
    assert result['unobservedMinutes'] == 0


def test_unobserved_gap_is_not_an_interruption_or_known_quality():
    result = nights.sleep_summary('2026-07-08', night_samples(gap=True))
    assert result['interruptions'] == 0
    assert result['unobservedMinutes'] == 15
    assert result['efficiency'] is None


def test_zero_length_awake_samples_are_not_interruptions():
    samples = night_samples()
    moment = at('2026-07-08', '06:00')
    samples.append((moment, moment, nights.AWAKE))
    assert nights.sleep_summary('2026-07-08', samples)['interruptions'] == 1


def test_in_bed_and_missing_nights_are_not_zero_sleep():
    samples = [(at('2026-07-08', '00:00'), at('2026-07-08', '08:00'), 0)]
    bed = nights.sleep_summary('2026-07-08', samples)
    assert bed['start'] is not None and bed['asleepMinutes'] is None
    assert bed['efficiency'] is None and bed['interruptions'] is None
    assert nights.sleep_summary('2026-07-08', [])['asleepMinutes'] is None


def test_sleep_endpoint_returns_all_seven_days_and_rejects_bad_dates(client):
    payload = [dict(uuid=str(uuid.uuid4()), type=nights.SLEEP_TYPE, kind='category',
                    start=s, end=e, value=v) for s, e, v in night_samples()]
    ingest.ingest({'samples': payload})
    response = client.get('/api/apple-health/sleep?to=2026-07-08')
    assert response.status_code == 200
    days = response.get_json()['days']
    assert len(days) == 7
    assert days[0]['date'] == '2026-07-02' and days[0]['asleepMinutes'] is None
    assert days[-1]['asleepMinutes'] == 465
    for bad in ('bad', '2026-02-30', '0001-01-01'):
        assert client.get(f'/api/apple-health/sleep?to={bad}').status_code == 400
