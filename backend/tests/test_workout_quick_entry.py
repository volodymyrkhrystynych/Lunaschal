import pytest

from backend.lifestyle.quick_entry import parse_entry
from backend.db.connection import get_db, _ensure_workout_quick_entry


@pytest.mark.parametrize('text,selected,weight,reps', [
    ('bicep curls 20, 10', None, 20, 10),
    ('20,10', 'bicep curl', 20, 10),
    ('squats 10', 'bicep curl', None, 10),
    ('curls 22.5 lbs x 8 reps', None, 22.5, 8),
])
def test_parse_sets(text, selected, weight, reps):
    parsed = parse_entry(text, selected)
    assert (parsed['weight'], parsed['reps']) == (weight, reps)


@pytest.mark.parametrize('text', ['20, 10', 'squats -10', 'curls 20 kg, 10',
                                  'curls 20, 1.5', 'walking 30, 10', 'cycling 0',
                                  'curls 20, 10 garbage', 'squat 10\ncurl 20,10'])
def test_invalid_entry(text):
    with pytest.raises(ValueError):
        parse_entry(text)


def log(client, text, exercise=None):
    response = client.post('/api/lifestyle/workouts/entries', json={'text': text, 'exercise': exercise})
    assert response.status_code == 201, response.get_json()
    return response.get_json()['session']


def test_grouping_cutoff_and_metadata_do_not_extend_workout(client, monkeypatch):
    now = [1800000000]
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: now[0])
    first = log(client, 'bicep curls 20, 10')
    assert first['locationType'] == 'unassigned'
    now[0] += 1800
    second = log(client, '20, 10', 'bicep curl')
    assert second['id'] == first['id']
    assert len(second['exercises'][0]['sets']) == 2
    assert second['durationMinutes'] == 30
    now[0] += 3599
    third = log(client, 'squats 10')
    assert third['id'] == first['id']
    now[0] += 3599
    assert client.patch('/api/lifestyle/workouts/' + first['id'], json={
        'locationType': 'building', 'intensityRating': 4}).status_code == 200
    now[0] += 1
    fourth = log(client, 'squats 10')
    assert fourth['id'] != first['id']
    old = client.get('/api/lifestyle/workouts/' + first['id']).get_json()
    assert old['locationType'] == 'building' and old['intensityRating'] == 4


def test_outdoor_is_separate_and_backdated(client, monkeypatch):
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: 1800000000)
    strength = log(client, 'squat 10')
    walk = log(client, 'walking 30 minutes')
    bike = log(client, '30', 'on the bike')
    assert len({strength['id'], walk['id'], bike['id']}) == 3
    assert walk['locationType'] == bike['locationType'] == 'outside'
    row = get_db().execute('SELECT * FROM workout_sessions WHERE id=?', (walk['id'],)).fetchone()
    assert row['ended_at'] - row['started_at'] == 1800
    assert walk['durationMinutes'] == 30
    assert walk['exercises'][0]['sets'] == []
    assert client.patch('/api/lifestyle/workouts/' + walk['id'], json={'locationType': 'building'}).status_code == 400
    assert client.post('/api/lifestyle/workouts/' + walk['id'] + '/reparse').status_code == 400


def test_recent_distinct_exercises_and_explicit_override(client):
    for name in ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta', 'iota', 'kappa', 'lambda']:
        log(client, name + ' 10')
    last = log(client, 'alpha 20, 10', 'beta')
    recent = client.get('/api/lifestyle/workouts/recent-exercises').get_json()
    assert len(recent) == 10
    assert recent[0]['name'] == 'alpha'
    assert last['exercises'][0]['sets'][-1]['weight'] == 20


def test_invalid_request_creates_nothing(client):
    assert client.post('/api/lifestyle/workouts/entries', json={'text': '20, 10'}).status_code == 400
    assert client.get('/api/lifestyle/workouts').get_json() == []


def test_migration_is_idempotent_and_preserves_legacy(client):
    db = get_db()
    for name in ['capture_kind', 'started_at', 'ended_at']:
        db.execute('ALTER TABLE workout_sessions DROP COLUMN ' + name)
    db.execute('ALTER TABLE workout_exercises DROP COLUMN logged_at')
    db.execute('ALTER TABLE workout_exercises DROP COLUMN logged_order')
    db.execute("INSERT INTO workout_sessions (id,date,location_type,raw_text,parse_status,created_at,updated_at) "
               "VALUES ('legacy','2026-09-19','building','squats 10','done',1,1)")
    _ensure_workout_quick_entry(db)
    _ensure_workout_quick_entry(db)
    assert 'ended_at' in {r[1] for r in db.execute('PRAGMA table_info(workout_sessions)')}
    row = db.execute("SELECT * FROM workout_sessions WHERE id='legacy'").fetchone()
    assert row['raw_text'] == 'squats 10'
    assert row['capture_kind'] is None and row['ended_at'] is None


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_a_queued_set_replays_once(client):
    body = {'text': 'bicep curls 20, 10', 'id': '01J0000000000000000000000A'}
    first = client.post('/api/lifestyle/workouts/entries', json=body).get_json()
    again = client.post('/api/lifestyle/workouts/entries', json=body).get_json()
    assert again['session']['id'] == first['session']['id']
    assert len(again['session']['exercises'][0]['sets']) == 1
    walk = {'text': 'walking 30', 'id': '01J0000000000000000000000B'}
    one = client.post('/api/lifestyle/workouts/entries', json=walk).get_json()['session']['id']
    two = client.post('/api/lifestyle/workouts/entries', json=walk).get_json()['session']['id']
    assert one == two == walk['id']
    assert get_db().execute("SELECT COUNT(*) FROM workout_sessions WHERE capture_kind='outdoor'").fetchone()[0] == 1


def test_sets_uploaded_later_group_by_when_they_were_done(client, monkeypatch):
    gym = 1800000000
    # Everything arrives in one burst, three hours after the first set.
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: gym + 3 * 3600)
    first = client.post('/api/lifestyle/workouts/entries', json={
        'text': 'squats 10', 'capturedAt': _iso(gym)}).get_json()['session']
    second = client.post('/api/lifestyle/workouts/entries', json={
        'text': 'squats 12', 'capturedAt': _iso(gym + 1800)}).get_json()['session']
    assert second['id'] == first['id'] and second['durationMinutes'] == 30
    later = client.post('/api/lifestyle/workouts/entries', json={
        'text': 'squats 8', 'capturedAt': _iso(gym + 1800 + 3601)}).get_json()['session']
    assert later['id'] != first['id']
    row = get_db().execute('SELECT started_at, ended_at FROM workout_sessions WHERE id=?', (first['id'],)).fetchone()
    assert (row['started_at'], row['ended_at']) == (gym, gym + 1800)


def test_a_bad_id_or_capture_time_is_refused(client):
    assert client.post('/api/lifestyle/workouts/entries', json={'text': 'squats 10', 'id': 'nope'}).status_code == 400
    assert client.post('/api/lifestyle/workouts/entries', json={'text': 'squats 10', 'capturedAt': 'yesterday'}).status_code == 400
    assert get_db().execute('SELECT COUNT(*) FROM workout_sessions').fetchone()[0] == 0
