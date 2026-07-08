"""Route tests for /api/habits (backend/routes/habits.py)."""
from datetime import date, timedelta


def make_habit(client, **overrides):
    body = {'name': 'meditate', **overrides}
    r = client.post('/api/habits', json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['id']


def get_habit(client, habit_id, archived=False):
    url = '/api/habits' + ('?archived=1' if archived else '')
    habits = client.get(url).get_json()
    return next((h for h in habits if h['id'] == habit_id), None)


def iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


# --- create / validation ---

def test_create_boolean_habit_defaults(client):
    habit_id = make_habit(client, name='  meditate  ')
    h = get_habit(client, habit_id)
    assert h['name'] == 'meditate'
    assert h['type'] == 'boolean'
    assert h['scheduleType'] == 'daily'
    assert h['archived'] is False
    assert h['currentStreak'] == 0
    assert h['todayStatus'] == 'none'
    assert h['todayScheduled'] is True
    assert h['streakUnit'] == 'days'


def test_create_quantity_habit(client):
    habit_id = make_habit(client, name='pushups', type='quantity', targetValue=25, unit='reps')
    h = get_habit(client, habit_id)
    assert h['type'] == 'quantity'
    assert h['targetValue'] == 25
    assert h['unit'] == 'reps'


def test_create_weekdays_habit(client):
    habit_id = make_habit(client, name='gym', scheduleType='weekdays', scheduleDays=[0, 2, 4])
    h = get_habit(client, habit_id)
    assert h['scheduleDays'] == [0, 2, 4]


def test_create_per_week_habit(client):
    habit_id = make_habit(client, name='run', scheduleType='per_week', timesPerWeek=3)
    h = get_habit(client, habit_id)
    assert h['timesPerWeek'] == 3
    assert h['streakUnit'] == 'weeks'


def test_create_validation_errors(client):
    assert client.post('/api/habits', json={'name': '  '}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'type': 'quantity'}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'type': 'quantity', 'targetValue': 0}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'scheduleType': 'weekdays'}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'scheduleType': 'weekdays', 'scheduleDays': [7]}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'scheduleType': 'per_week'}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'scheduleType': 'per_week', 'timesPerWeek': 0}).status_code == 400
    assert client.post('/api/habits', json={'name': 'x', 'scheduleType': 'per_week', 'timesPerWeek': 8}).status_code == 400


# --- update / archive / reorder / delete ---

def test_patch_updates_fields(client):
    habit_id = make_habit(client)
    r = client.patch(f'/api/habits/{habit_id}', json={'name': 'meditate daily', 'color': '#22c55e'})
    assert r.status_code == 200
    h = get_habit(client, habit_id)
    assert h['name'] == 'meditate daily'
    assert h['color'] == '#22c55e'


def test_patch_unknown_habit_404(client):
    assert client.patch('/api/habits/nope', json={'name': 'x'}).status_code == 404


def test_archive_hides_from_default_list(client):
    habit_id = make_habit(client)
    client.patch(f'/api/habits/{habit_id}', json={'archived': True})
    assert get_habit(client, habit_id) is None
    h = get_habit(client, habit_id, archived=True)
    assert h['archived'] is True
    client.patch(f'/api/habits/{habit_id}', json={'archived': False})
    assert get_habit(client, habit_id) is not None


def test_reorder(client):
    a = make_habit(client, name='a')
    b = make_habit(client, name='b')
    r = client.post('/api/habits/reorder', json={'order': [b, a]})
    assert r.status_code == 200
    habits = client.get('/api/habits').get_json()
    assert [h['id'] for h in habits] == [b, a]


def test_delete_cascades_checks(client):
    habit_id = make_habit(client)
    client.put(f'/api/habits/{habit_id}/checks/{iso(0)}', json={'status': 'done'})
    assert client.delete(f'/api/habits/{habit_id}').status_code == 200
    checks = client.get(f'/api/habits/checks?from={iso(7)}&to={iso(0)}').get_json()
    assert checks == []


def test_delete_unknown_404(client):
    assert client.delete('/api/habits/nope').status_code == 404


# --- checks ---

def test_check_upsert_cycle(client):
    habit_id = make_habit(client)
    today = iso(0)
    assert client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'done'}).status_code == 200
    assert get_habit(client, habit_id)['todayStatus'] == 'done'

    client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'skipped'})
    assert get_habit(client, habit_id)['todayStatus'] == 'skipped'

    client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'none'})
    assert get_habit(client, habit_id)['todayStatus'] == 'none'


def test_check_validation(client):
    habit_id = make_habit(client)
    today = iso(0)
    assert client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'maybe'}).status_code == 400
    assert client.put(f'/api/habits/{habit_id}/checks/not-a-date', json={'status': 'done'}).status_code == 400
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.put(f'/api/habits/{habit_id}/checks/{tomorrow}', json={'status': 'done'}).status_code == 400
    assert client.put(f'/api/habits/nope/checks/{today}', json={'status': 'done'}).status_code == 404


def test_quantity_check_requires_value(client):
    habit_id = make_habit(client, name='pushups', type='quantity', targetValue=25)
    today = iso(0)
    assert client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'done'}).status_code == 400

    client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'done', 'value': 10})
    h = get_habit(client, habit_id)
    assert h['todayValue'] == 10
    assert h['todaySatisfied'] is False

    client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'done', 'value': 25})
    assert get_habit(client, habit_id)['todaySatisfied'] is True

    # value <= 0 clears the check
    client.put(f'/api/habits/{habit_id}/checks/{today}', json={'status': 'done', 'value': 0})
    assert get_habit(client, habit_id)['todayStatus'] == 'none'


def test_backdated_checks_build_streak(client):
    habit_id = make_habit(client)
    for n in (2, 1, 0):
        client.put(f'/api/habits/{habit_id}/checks/{iso(n)}', json={'status': 'done'})
    h = get_habit(client, habit_id)
    assert h['currentStreak'] == 3
    assert h['longestStreak'] == 3


def test_checks_range_endpoint(client):
    habit_id = make_habit(client)
    client.put(f'/api/habits/{habit_id}/checks/{iso(10)}', json={'status': 'done'})
    client.put(f'/api/habits/{habit_id}/checks/{iso(1)}', json={'status': 'skipped'})

    checks = client.get(f'/api/habits/checks?from={iso(5)}&to={iso(0)}').get_json()
    assert len(checks) == 1
    assert checks[0]['habitId'] == habit_id
    assert checks[0]['date'] == iso(1)
    assert checks[0]['status'] == 'skipped'

    assert client.get('/api/habits/checks').status_code == 400
    assert client.get(f'/api/habits/checks?from={iso(0)}&to={iso(5)}').status_code == 400


def test_checks_include_archived_history(client):
    habit_id = make_habit(client)
    client.put(f'/api/habits/{habit_id}/checks/{iso(1)}', json={'status': 'done'})
    client.patch(f'/api/habits/{habit_id}', json={'archived': True})
    checks = client.get(f'/api/habits/checks?from={iso(7)}&to={iso(0)}').get_json()
    assert len(checks) == 1
