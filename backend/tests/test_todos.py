"""Route tests for the one-off todo list CRUD (`backend/routes/tasks.py`).

Exercises the handlers against a real temporary SQLite DB via the Flask test
client — covering title trimming/validation, the done toggle, completed_at
tracking, done-last ordering, delete, and the legacy-DB migration.
"""
import sqlite3
from datetime import datetime, timezone

from backend.db import connection


def test_create_trims_title_and_defaults_undone(client):
    r = client.post('/api/tasks/todos', json={'title': '  buy milk  '})
    assert r.status_code == 201
    todo_id = r.get_json()['id']
    assert todo_id

    todos = client.get('/api/tasks/todos').get_json()
    assert len(todos) == 1
    assert todos[0]['id'] == todo_id
    assert todos[0]['title'] == 'buy milk'  # surrounding whitespace stripped
    assert todos[0]['done'] is False        # coerced to a JSON boolean, not 0


def test_create_rejects_blank_title(client):
    r = client.post('/api/tasks/todos', json={'title': '   '})
    assert r.status_code == 400
    assert client.get('/api/tasks/todos').get_json() == []


def test_toggle_done_puts_it_last_in_the_list(client):
    first = client.post('/api/tasks/todos', json={'title': 'first'}).get_json()['id']
    client.post('/api/tasks/todos', json={'title': 'second'})

    r = client.patch(f'/api/tasks/todos/{first}', json={'done': True})
    assert r.status_code == 200

    titles = [t['title'] for t in client.get('/api/tasks/todos').get_json()]
    # ORDER BY done, created_at → the completed "first" sinks below "second"
    assert titles == ['second', 'first']


def test_rename_and_empty_update_are_handled(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'old'}).get_json()['id']

    assert client.patch(f'/api/tasks/todos/{todo_id}', json={'title': 'new'}).status_code == 200
    assert client.get('/api/tasks/todos').get_json()[0]['title'] == 'new'

    # A rename to blank is rejected; an update with no fields is a no-op error.
    assert client.patch(f'/api/tasks/todos/{todo_id}', json={'title': '  '}).status_code == 400
    assert client.patch(f'/api/tasks/todos/{todo_id}', json={}).status_code == 400


def test_delete_removes_the_todo(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'temp'}).get_json()['id']
    assert client.delete(f'/api/tasks/todos/{todo_id}').status_code == 200
    assert client.get('/api/tasks/todos').get_json() == []


def test_completing_sets_completed_at_and_uncompleting_clears_it(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'x'}).get_json()['id']
    assert client.get('/api/tasks/todos').get_json()[0]['completedAt'] is None

    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})
    done = client.get('/api/tasks/todos').get_json()[0]
    assert done['done'] is True
    assert done['completedAt'] is not None  # ISO timestamp of the completion

    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': False})
    undone = client.get('/api/tasks/todos').get_json()[0]
    assert undone['done'] is False
    assert undone['completedAt'] is None


def test_recompleting_keeps_the_original_completion_time(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'x'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    # Pin the completion time to a known past value, then mark done again.
    connection.get_db().execute('UPDATE todos SET completed_at=100 WHERE id=?', (todo_id,))
    connection.get_db().commit()
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})

    got = client.get('/api/tasks/todos').get_json()[0]['completedAt']
    assert got == datetime.fromtimestamp(100, tz=timezone.utc).isoformat()


def test_renaming_does_not_touch_completed_at(client):
    todo_id = client.post('/api/tasks/todos', json={'title': 'x'}).get_json()['id']
    client.patch(f'/api/tasks/todos/{todo_id}', json={'done': True})
    before = client.get('/api/tasks/todos').get_json()[0]['completedAt']

    client.patch(f'/api/tasks/todos/{todo_id}', json={'title': 'renamed'})
    after = client.get('/api/tasks/todos').get_json()[0]
    assert after['title'] == 'renamed'
    assert after['completedAt'] == before


def test_migration_backfills_completed_at_on_legacy_dbs(tmp_path):
    # A DB created before the completed_at column existed: done todos should
    # inherit updated_at as their best-guess completion time.
    db_path = str(tmp_path / 'legacy.db')
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        'CREATE TABLE todos ('
        ' id TEXT PRIMARY KEY, title TEXT NOT NULL,'
        ' done INTEGER NOT NULL DEFAULT 0,'
        ' created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)'
    )
    legacy.execute("INSERT INTO todos VALUES ('t1', 'finished thing', 1, 100, 200)")
    legacy.execute("INSERT INTO todos VALUES ('t2', 'open thing', 0, 100, 150)")
    legacy.commit()
    legacy.close()

    prev_path, prev_conn = connection._DB_PATH, connection._conn
    if prev_conn is not None:
        prev_conn.close()
    connection._DB_PATH, connection._conn = db_path, None
    try:
        connection.init_db()
        rows = {r['id']: r for r in connection.get_db().execute('SELECT * FROM todos')}
        assert rows['t1']['completed_at'] == 200
        assert rows['t2']['completed_at'] is None
    finally:
        if connection._conn is not None:
            connection._conn.close()
        connection._DB_PATH, connection._conn = prev_path, prev_conn
