"""Route tests for the Chat tab's to-do bar (`/api/tasks/chat-todos`):
day-scoped, ephemeral to-dos written instantly by the chat delegate's
add_todos tool and the morning briefing — this file exercises the CRUD/
promote surface directly, not those two writers."""
import time

from backend.db import connection
from backend.day_boundary import day_key_for


def test_add_writes_rows_instantly_with_no_confirm_step(client):
    resp = client.post('/api/tasks/chat-todos', json={
        'items': [{'title': 'Buy milk'}, {'title': 'Call the dentist', 'notes': 'filling'}],
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert [t['title'] for t in body] == ['Buy milk', 'Call the dentist']
    assert body[1]['notes'] == 'filling'
    assert all(t['done'] is False for t in body)

    listed = client.get('/api/tasks/chat-todos').get_json()
    assert [t['title'] for t in listed] == ['Buy milk', 'Call the dentist']


def test_add_requires_a_non_empty_items_list(client):
    assert client.post('/api/tasks/chat-todos', json={'items': []}).status_code == 400
    assert client.post('/api/tasks/chat-todos', json={}).status_code == 400


def test_add_skips_a_blank_title_but_keeps_the_rest(client):
    resp = client.post('/api/tasks/chat-todos', json={
        'items': [{'title': '   '}, {'title': 'Buy milk'}],
    })
    assert [t['title'] for t in resp.get_json()] == ['Buy milk']


def test_add_is_capped_at_ten_items(client):
    items = [{'title': f't{i}'} for i in range(15)]
    resp = client.post('/api/tasks/chat-todos', json={'items': items})
    assert len(resp.get_json()) == 10


def test_add_skips_a_title_already_in_todays_bar(client):
    client.post('/api/tasks/chat-todos', json={'items': [{'title': 'Buy milk'}]})
    resp = client.post('/api/tasks/chat-todos', json={'items': [{'title': 'buy MILK'}]})
    assert resp.get_json() == []
    assert len(client.get('/api/tasks/chat-todos').get_json()) == 1


def test_list_only_shows_todays_day_key(client):
    """A row seeded under yesterday's day_key doesn't show up today — the bar
    resets at the day boundary by construction, no purge needed."""
    yesterday = day_key_for(int(time.time()) - 86400)
    db = connection.get_db()
    db.execute(
        'INSERT INTO chat_todos(id, day_key, title, priority, done, created_at, updated_at)'
        " VALUES ('old', ?, 'Stale item', 3, 0, 0, 0)",
        (yesterday,),
    )
    db.commit()

    assert client.get('/api/tasks/chat-todos').get_json() == []


def test_update_title(client):
    added = client.post('/api/tasks/chat-todos', json={'items': [{'title': 'Buy milk'}]}).get_json()
    chat_todo_id = added[0]['id']

    resp = client.patch(f'/api/tasks/chat-todos/{chat_todo_id}', json={'title': 'Buy oat milk'})
    assert resp.status_code == 200
    assert client.get('/api/tasks/chat-todos').get_json()[0]['title'] == 'Buy oat milk'


def test_update_rejects_a_blank_title(client):
    added = client.post('/api/tasks/chat-todos', json={'items': [{'title': 'Buy milk'}]}).get_json()
    resp = client.patch(f'/api/tasks/chat-todos/{added[0]["id"]}', json={'title': '   '})
    assert resp.status_code == 400


def test_update_unknown_id_404s(client):
    resp = client.patch('/api/tasks/chat-todos/nope', json={'title': 'x'})
    assert resp.status_code == 404


def test_delete_removes_the_row_and_logs_nothing(client):
    added = client.post('/api/tasks/chat-todos', json={'items': [{'title': 'Buy milk'}]}).get_json()
    resp = client.delete(f'/api/tasks/chat-todos/{added[0]["id"]}')
    assert resp.status_code == 200
    assert client.get('/api/tasks/chat-todos').get_json() == []
    # Dismissing is not completing — nothing shows up in the Journal feed for it.
    assert client.get('/api/tasks/events').get_json() == []


def test_promote_inserts_a_permanent_todo_and_removes_the_source_row(client):
    added = client.post(
        '/api/tasks/chat-todos', json={'items': [{'title': 'Buy milk'}]}
    ).get_json()
    chat_todo_id = added[0]['id']

    resp = client.post(f'/api/tasks/chat-todos/{chat_todo_id}/promote', json={
        'title': 'Buy oat milk', 'notes': 'the barista brand', 'priority': 4,
        'list': 'todo',
    })
    assert resp.status_code == 201
    todo_id = resp.get_json()['id']

    todos = client.get('/api/tasks/todos').get_json()
    assert [(t['id'], t['title'], t['priority']) for t in todos] == [
        (todo_id, 'Buy oat milk', 4)
    ]
    # The ephemeral row is gone — it's been moved, not copied.
    assert client.get('/api/tasks/chat-todos').get_json() == []


def test_promote_with_invalid_data_400s_and_leaves_the_source_row_intact(client):
    added = client.post(
        '/api/tasks/chat-todos', json={'items': [{'title': 'Buy milk'}]}
    ).get_json()
    chat_todo_id = added[0]['id']

    resp = client.post(
        f'/api/tasks/chat-todos/{chat_todo_id}/promote', json={'title': ''}
    )
    assert resp.status_code == 400
    assert client.get('/api/tasks/todos').get_json() == []
    # No partial state: the chat-todo row is still there, untouched.
    assert [t['title'] for t in client.get('/api/tasks/chat-todos').get_json()] == ['Buy milk']


def test_promote_unknown_id_404s(client):
    resp = client.post('/api/tasks/chat-todos/nope/promote', json={'title': 'x'})
    assert resp.status_code == 404
