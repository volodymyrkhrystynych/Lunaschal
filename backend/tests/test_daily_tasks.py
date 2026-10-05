"""Route tests for daily tasks (`backend/routes/tasks.py`), focused on the
4am day boundary: completion is recorded against the 4am-anchored day, not
literal midnight, and a completion from "yesterday" (per that boundary)
doesn't count once the day has rolled over."""
import time

from backend.db import connection


def _create_task(client, title='Meditate'):
    r = client.post('/api/tasks', json={'title': title})
    assert r.status_code == 201
    return r.get_json()['id']


def test_complete_records_against_day_key_for(client, monkeypatch):
    monkeypatch.setattr(
        'backend.routes.tasks.day_key_for', lambda ts=None: '2026-07-24'
    )
    task_id = _create_task(client)
    assert client.post(f'/api/tasks/{task_id}/complete').status_code == 200

    row = connection.get_db().execute(
        'SELECT date FROM daily_task_completions WHERE task_id=?', (task_id,)
    ).fetchone()
    assert row['date'] == '2026-07-24'

    tasks = client.get('/api/tasks').get_json()
    assert tasks[0]['done'] == 1


def test_completion_before_4am_does_not_count_for_the_new_day(client, monkeypatch):
    """A completion recorded at 2am (still the previous 4am-day) must not show
    as done once the clock crosses into the next 4am-day."""
    monkeypatch.setattr(
        'backend.routes.tasks.day_key_for', lambda ts=None: '2026-07-24'
    )
    task_id = _create_task(client)
    client.post(f'/api/tasks/{task_id}/complete')
    assert client.get('/api/tasks').get_json()[0]['done'] == 1

    # The 4am boundary passes; day_key_for now reports the new day.
    monkeypatch.setattr(
        'backend.routes.tasks.day_key_for', lambda ts=None: '2026-07-25'
    )
    assert client.get('/api/tasks').get_json()[0]['done'] == 0


def test_uncomplete_retracts_only_todays_notification(client, monkeypatch):
    monkeypatch.setattr(
        'backend.routes.tasks.day_key_for', lambda ts=None: '2026-07-24'
    )
    task_id = _create_task(client)
    client.post(f'/api/tasks/{task_id}/complete')

    events = connection.get_db().execute(
        "SELECT COUNT(*) FROM task_events WHERE kind='daily_completed' AND ref_id=?",
        (task_id,),
    ).fetchone()[0]
    assert events == 1

    assert client.delete(f'/api/tasks/{task_id}/complete').status_code == 200
    remaining = connection.get_db().execute(
        "SELECT COUNT(*) FROM task_events WHERE kind='daily_completed' AND ref_id=?",
        (task_id,),
    ).fetchone()[0]
    assert remaining == 0
    assert client.get('/api/tasks').get_json()[0]['done'] == 0


def test_uncomplete_leaves_a_prior_days_notification_alone(client, monkeypatch):
    """`uncomplete_task` bounds its cleanup to the current 4am-day's window
    (day_bounds), so retracting today's completion can't reach back and erase
    a notification logged before the day rolled over."""
    monkeypatch.setattr(
        'backend.routes.tasks.day_key_for', lambda ts=None: '2026-07-24'
    )
    task_id = _create_task(client)

    # A stale completion event from well before today's 4am boundary.
    from backend.day_boundary import day_bounds
    start, _ = day_bounds('2026-07-24')
    db = connection.get_db()
    db.execute(
        "INSERT INTO task_events(id, kind, title, ref_id, task_list, created_at)"
        " VALUES ('old_evt', 'daily_completed', 'Meditate', ?, 'daily', ?)",
        (task_id, start - 3600),
    )
    db.commit()

    client.post(f'/api/tasks/{task_id}/complete')
    assert client.delete(f'/api/tasks/{task_id}/complete').status_code == 200

    remaining = connection.get_db().execute(
        "SELECT id FROM task_events WHERE kind='daily_completed' AND ref_id=?",
        (task_id,),
    ).fetchall()
    assert [r['id'] for r in remaining] == ['old_evt']


def test_a_tick_replayed_after_the_rollover_lands_on_its_own_day(client, monkeypatch):
    """The phone queues a tick offline with the day it was made; sent after
    4am, it still counts for that day, and its notification is filed in it."""
    from backend.day_boundary import day_bounds
    monkeypatch.setattr(
        'backend.routes.tasks.day_key_for', lambda ts=None: '2026-07-25'
    )
    task_id = _create_task(client)
    assert client.post(f'/api/tasks/{task_id}/complete?date=2026-07-24').status_code == 200

    db = connection.get_db()
    row = db.execute(
        'SELECT date FROM daily_task_completions WHERE task_id=?', (task_id,)
    ).fetchone()
    assert row['date'] == '2026-07-24'
    assert client.get('/api/tasks').get_json()[0]['done'] == 0, 'Not done today'
    stamped = db.execute(
        "SELECT created_at FROM task_events WHERE kind='daily_completed' AND ref_id=?",
        (task_id,),
    ).fetchone()['created_at']
    start, end = day_bounds('2026-07-24')
    assert start <= stamped < end

    # Today's tick, then clearing yesterday's: today's notification stays.
    client.post(f'/api/tasks/{task_id}/complete')
    assert client.delete(f'/api/tasks/{task_id}/complete?date=2026-07-24').status_code == 200
    days = [r['date'] for r in db.execute(
        'SELECT date FROM daily_task_completions WHERE task_id=?', (task_id,)
    )]
    assert days == ['2026-07-25']
    events = db.execute(
        "SELECT COUNT(*) FROM task_events WHERE kind='daily_completed' AND ref_id=?",
        (task_id,),
    ).fetchone()[0]
    assert events == 1


def test_a_bad_date_is_refused(client):
    task_id = _create_task(client)
    assert client.post(f'/api/tasks/{task_id}/complete?date=yesterday').status_code == 400
    assert client.delete(f'/api/tasks/{task_id}/complete?date=2026-02-30').status_code == 400


def test_a_client_id_makes_a_repeated_create_a_no_op(client):
    """An offline-created task carries its own id, so a replay of a create the
    server already took answers 201 again, even once the list is full."""
    ids = [f'01JTASK{n}AAAAAAAAAAAAAAAAAA'[:26] for n in range(4)]
    for n, task_id in enumerate(ids):
        r = client.post('/api/tasks', json={'id': task_id, 'title': f'Task {n}'})
        assert r.status_code == 201
        assert r.get_json()['id'] == task_id
    again = client.post('/api/tasks', json={'id': ids[3], 'title': 'Task 3'})
    assert again.status_code == 201
    assert len(client.get('/api/tasks').get_json()) == 4
    assert client.post('/api/tasks', json={'title': 'Fifth'}).status_code == 400
    assert client.post('/api/tasks', json={'id': '../x', 'title': 'Bad'}).status_code == 400
