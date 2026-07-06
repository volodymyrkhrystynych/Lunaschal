"""Route tests for the one-off todo list CRUD (`backend/routes/tasks.py`).

Exercises the handlers against a real temporary SQLite DB via the Flask test
client — covering title trimming/validation, the done toggle, done-last
ordering, and delete.
"""


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
