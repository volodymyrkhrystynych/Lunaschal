"""Chat-driven quick actions: confirming a calorie log or a new task from the
accept/deny card, mirroring the existing /api/chat/save-calendar pattern."""


def _make_message(client):
    conv = client.post('/api/chat/conversations', json={'mode': 'chat'}).get_json()
    msg = client.post(
        f'/api/chat/conversations/{conv["id"]}/messages',
        json={'role': 'user', 'content': 'I ate a burger, 650 calories'},
    ).get_json()
    return msg['id']


def test_save_calories_inserts_row_and_stamps_message(client):
    message_id = _make_message(client)
    resp = client.post('/api/chat/save-calories', json={
        'description': 'burger', 'calories': 650, 'date': '2026-07-20', 'messageId': message_id,
    })
    assert resp.status_code == 201
    entry_id = resp.get_json()['id']

    day = client.get('/api/lifestyle/calories?date=2026-07-20').get_json()
    assert day['entries'] == [{
        'id': entry_id, 'date': '2026-07-20', 'description': 'burger', 'calories': 650,
        'createdAt': day['entries'][0]['createdAt'],
    }]

    conv = client.get('/api/chat/today?mode=chat').get_json()
    saved_msg = next(m for m in conv['messages'] if m['id'] == message_id)
    import json
    assert json.loads(saved_msg['metadata'])['savedAsCalories'] == entry_id


def test_save_calories_rejects_missing_description(client):
    assert client.post('/api/chat/save-calories', json={'calories': 400}).status_code == 400


def test_save_calories_rejects_bad_calories(client):
    assert client.post(
        '/api/chat/save-calories', json={'description': 'burger', 'calories': 'lots'}
    ).status_code == 400


def test_save_task_inserts_todo_and_stamps_message(client):
    message_id = _make_message(client)
    resp = client.post('/api/chat/save-task', json={
        'title': 'call the dentist', 'messageId': message_id,
    })
    assert resp.status_code == 201
    todo_id = resp.get_json()['id']

    todos = client.get('/api/tasks/todos').get_json()
    assert [t['title'] for t in todos] == ['call the dentist']
    assert todos[0]['list'] == 'todo'
    assert todos[0]['done'] is False

    conv = client.get('/api/chat/today?mode=chat').get_json()
    saved_msg = next(m for m in conv['messages'] if m['id'] == message_id)
    import json
    assert json.loads(saved_msg['metadata'])['savedAsTask'] == todo_id


def test_save_task_accepts_named_list(client):
    resp = client.post('/api/chat/save-task', json={'title': 'vacuum', 'list': 'chores'})
    assert resp.status_code == 201
    todos = client.get('/api/tasks/todos?list=chores').get_json()
    assert [t['title'] for t in todos] == ['vacuum']


def test_save_task_rejects_missing_title(client):
    assert client.post('/api/chat/save-task', json={}).status_code == 400


def test_save_task_rejects_invalid_list(client):
    assert client.post(
        '/api/chat/save-task', json={'title': 'x', 'list': 'nonsense'}
    ).status_code == 400
