from backend.db import connection
from backend.day_boundary import day_key_for


def test_grouping_survives_connection_restart(client, monkeypatch):
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: 1800000000)
    first = client.post('/api/lifestyle/workouts/entries', json={'text': 'curls 20,10'}).get_json()['session']
    connection.get_db().close()
    connection._conn = None
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: 1800001800)
    second = client.post('/api/lifestyle/workouts/entries', json={'text': 'curls 20,10'}).get_json()['session']
    assert first['id'] == second['id']
    assert len(second['exercises'][0]['sets']) == 2


def test_outdoor_date_uses_backdated_start(client, monkeypatch):
    from datetime import datetime
    end = int(datetime(2026, 9, 20, 4, 15).timestamp())
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: end)
    result = client.post('/api/lifestyle/workouts/entries', json={'text': 'walking 30'}).get_json()['session']
    assert result['date'] == day_key_for(end - 1800)
    assert result['date'] == '2026-09-19'


def test_recent_includes_legacy_and_new_sets_do_not_merge_into_it(client):
    from ulid import ULID
    from backend.routes.lifestyle import _replace_exercises
    import time
    db = connection.get_db()
    sid = str(ULID())
    db.execute("INSERT INTO workout_sessions (id,date,location_type,parse_status,created_at,updated_at) VALUES (?,?,?,'done',?,?)",
               (sid, day_key_for(), 'building', int(time.time()), int(time.time())))
    _replace_exercises(db, sid, [{'name': 'curls', 'sets': [{'weight': 20, 'reps': 10}]}])
    db.commit()
    recent = client.get('/api/lifestyle/workouts/recent-exercises').get_json()
    assert recent[0]['name'] == 'curl'
    new = client.post('/api/lifestyle/workouts/entries', json={'text': 'curl 20,10'}).get_json()['session']
    assert new['id'] != sid
