"""Route tests for the task-event log (`backend/routes/tasks.py`).

Completing or deleting todos and daily tasks appends small notifications to the
`task_events` table, which the Journal feed interleaves. These exercise the
handlers against the real temporary SQLite DB via the Flask test client.
"""
import sqlite3
import time

from ulid import ULID

from backend.db import connection


def _events(client, **params):
    return client.get('/api/tasks/events', query_string=params).get_json()


# --- One-off todos ---------------------------------------------------------


def test_completing_a_todo_logs_one_event(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'buy milk'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    events = _events(client)
    assert len(events) == 1
    ev = events[0]
    assert ev['kind'] == 'todo_completed'
    assert ev['title'] == 'buy milk'
    assert ev['refId'] == todo_id
    assert ev['taskList'] == 'todo'  # default list


def test_event_records_the_todos_list(client):
    todo_id = client.post(
        '/api/tasks/todos', json={'title': 'sweep', 'list': 'archive'}
    ).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    assert _events(client)[0]['taskList'] == 'archive'


def test_an_old_chores_event_still_reads_back(client):
    """The chores list is retired, but `task_events.task_list` is history and was
    deliberately left alone by the migration — those completions really did come
    off a Chores list, and the Journal feed still labels them that way."""
    now = int(time.time())
    connection.get_db().execute(
        'INSERT INTO task_events(id, kind, title, ref_id, task_list, created_at)'
        " VALUES (?, 'todo_completed', 'sweep', ?, 'chores', ?)",
        (str(ULID()), str(ULID()), now),
    )
    connection.get_db().commit()

    assert _events(client)[0]['taskList'] == 'chores'


def test_event_snapshots_the_todos_notes_as_detail(client):
    todo_id = client.post(
        '/api/tasks/todos',
        json={'title': 'call plumber', 'notes': 'leak under the sink, ask about parts'},
    ).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    assert _events(client)[0]['detail'] == 'leak under the sink, ask about parts'


def test_deletion_event_snapshots_notes_so_they_survive(client):
    todo_id = client.post(
        '/api/tasks/todos', json={'title': 'old plan', 'notes': 'the details'}
    ).get_json()['id']
    client.delete(f'/api/tasks/todos/{todo_id}')  # todo row is gone after this

    assert _events(client)[0]['detail'] == 'the details'


def test_event_detail_is_null_without_notes(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'bare'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    assert _events(client)[0]['detail'] is None


def test_uncompleting_a_todo_retracts_its_event(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'buy milk'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})
    assert len(_events(client)) == 1

    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': False})
    assert _events(client) == []


def test_completing_an_already_done_todo_does_not_double_log(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'buy milk'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    assert len(_events(client)) == 1


def test_deleting_an_active_todo_logs_a_removal(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'drop this'}).get_json()['id']
    client.delete(f'/api/tasks/todos/{todo_id}')

    events = _events(client)
    assert len(events) == 1
    assert events[0]['kind'] == 'task_deleted'
    assert events[0]['title'] == 'drop this'


def test_deleting_an_already_done_todo_does_not_log_a_removal(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'done thing'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})
    client.delete(f'/api/tasks/todos/{todo_id}')

    # Only the completion event survives — no "removed" noise for a done item.
    kinds = [e['kind'] for e in _events(client)]
    assert kinds == ['todo_completed']


def test_repeating_todo_completion_logs_each_occurrence(client):
    todo_id = client.post(
        '/api/tasks/todos',
        json={'title': 'water plants', 'repeatInterval': 1, 'repeatUnit': 'day'},
    ).get_json()['id']

    # A repeating todo resets to not-done on completion, so it can be completed
    # again — each is a genuine occurrence and logs its own event.
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    events = _events(client)
    assert [e['kind'] for e in events] == ['todo_completed', 'todo_completed']
    assert client.get('/api/tasks/todos').get_json()[0]['done'] is False


def test_events_are_newest_first(client):
    a = client.post('/api/tasks/todos', json={'title': 'a'}).get_json()['id']
    b = client.post('/api/tasks/todos', json={'title': 'b'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{a}', json={'done': True})
    client.patch(f'/api/tasks/todos/{b}', json={'done': True})

    titles = [e['title'] for e in _events(client)]
    assert titles == ['b', 'a']


def test_events_respect_the_limit_param(client):
    for i in range(3):
        tid = client.post('/api/tasks/todos', json={'title': f't{i}'}).get_json()['id']
        client.patch(f'/api/tasks/todos/{tid}', json={'done': True})

    assert len(_events(client, limit=2)) == 2


# --- Chat to-dos (the day-scoped bar above the chat input) -----------------


def test_completing_a_chat_todo_logs_one_event(client):
    added = client.post(
        '/api/tasks/chat-todos', json={'items': [{'title': 'call the dentist'}]}
    ).get_json()
    chat_todo_id = added[0]['id']
    client.patch(f'/api/tasks/chat-todos/{chat_todo_id}', json={'done': True})

    events = _events(client)
    assert len(events) == 1
    ev = events[0]
    assert ev['kind'] == 'chat_todo_completed'
    assert ev['title'] == 'call the dentist'
    assert ev['refId'] == chat_todo_id
    assert ev['taskList'] == 'chat'


def test_chat_todo_event_snapshots_notes_as_detail(client):
    added = client.post(
        '/api/tasks/chat-todos',
        json={'items': [{'title': 'call plumber', 'notes': 'ask about parts'}]},
    ).get_json()
    chat_todo_id = added[0]['id']
    client.patch(f'/api/tasks/chat-todos/{chat_todo_id}', json={'done': True})

    assert _events(client)[0]['detail'] == 'ask about parts'


def test_uncompleting_a_chat_todo_retracts_its_event(client):
    added = client.post(
        '/api/tasks/chat-todos', json={'items': [{'title': 'call the dentist'}]}
    ).get_json()
    chat_todo_id = added[0]['id']
    client.patch(f'/api/tasks/chat-todos/{chat_todo_id}', json={'done': True})
    assert len(_events(client)) == 1

    client.patch(f'/api/tasks/chat-todos/{chat_todo_id}', json={'done': False})
    assert _events(client) == []


# --- Daily tasks -----------------------------------------------------------


def test_completing_a_daily_task_logs_one_event(client):
    task_id = client.post('/api/tasks', json={'title': 'stretch'}).get_json()['id']
    client.post(f'/api/tasks/{task_id}/complete')

    events = _events(client)
    assert len(events) == 1
    assert events[0]['kind'] == 'daily_completed'
    assert events[0]['title'] == 'stretch'
    assert events[0]['refId'] == task_id
    assert events[0]['taskList'] == 'daily'


def test_re_completing_a_daily_task_does_not_double_log(client):
    task_id = client.post('/api/tasks', json={'title': 'stretch'}).get_json()['id']
    client.post(f'/api/tasks/{task_id}/complete')
    client.post(f'/api/tasks/{task_id}/complete')  # idempotent re-post

    assert len(_events(client)) == 1


def test_uncompleting_a_daily_task_retracts_todays_event(client):
    task_id = client.post('/api/tasks', json={'title': 'stretch'}).get_json()['id']
    client.post(f'/api/tasks/{task_id}/complete')
    assert len(_events(client)) == 1

    client.delete(f'/api/tasks/{task_id}/complete')
    assert _events(client) == []


def test_deleting_a_daily_task_logs_a_removal(client):
    task_id = client.post('/api/tasks', json={'title': 'stretch'}).get_json()['id']
    client.delete(f'/api/tasks/{task_id}')

    events = _events(client)
    assert len(events) == 1
    assert events[0]['kind'] == 'task_deleted'
    assert events[0]['title'] == 'stretch'
    assert events[0]['taskList'] == 'daily'


# --- Migration -------------------------------------------------------------


def test_migration_adds_columns_to_legacy_event_table(tmp_path):
    # A DB whose task_events table predates the task_list/detail columns: the
    # migration must add them (idempotently) so the live dev DB upgrades cleanly.
    db_path = str(tmp_path / 'legacy.db')
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        'CREATE TABLE task_events ('
        ' id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,'
        ' ref_id TEXT, created_at INTEGER NOT NULL)'
    )
    legacy.execute(
        "INSERT INTO task_events VALUES ('e1', 'todo_completed', 'old', NULL, 100)"
    )
    legacy.commit()
    legacy.close()

    prev_path, prev_conn = connection._DB_PATH, connection._conn
    if prev_conn is not None:
        prev_conn.close()
    connection._DB_PATH, connection._conn = db_path, None
    try:
        connection.init_db()
        cols = {
            r[1]
            for r in connection.get_db().execute('PRAGMA table_info(task_events)')
        }
        assert {'task_list', 'detail'} <= cols
        # The pre-existing row survives with the new columns NULL.
        row = connection.get_db().execute(
            'SELECT task_list, detail FROM task_events WHERE id=?', ('e1',)
        ).fetchone()
        assert row['task_list'] is None
        assert row['detail'] is None
    finally:
        if connection._conn is not None:
            connection._conn.close()
        connection._DB_PATH, connection._conn = prev_path, prev_conn
