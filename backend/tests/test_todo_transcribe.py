"""Tests for POST /api/tasks/todos/transcribe."""

from unittest.mock import patch

from backend.routes.tasks import bp


def test_transcribe_todo_requires_text(client):
    """Empty text returns 400."""
    r = client.post('/api/tasks/todos/transcribe', json={'text': ''})
    assert r.status_code == 400
    assert 'text required' in r.get_json()['error']

    r = client.post('/api/tasks/todos/transcribe', json={})
    assert r.status_code == 400


def test_transcribe_todo_minimal(client):
    """A bare sentence creates a todo with the raw text as title when LLM is
    unavailable."""
    with patch('backend.routes.tasks.extract_todo_fields', return_value=None):
        r = client.post('/api/tasks/todos/transcribe', json={'text': 'Buy milk'})
        assert r.status_code == 201
        data = r.get_json()
        assert 'id' in data

    # Verify the todo was created with the raw transcript as title
    r = client.get('/api/tasks/todos')
    todos = r.get_json()
    assert any(t['title'] == 'Buy milk' for t in todos)


def test_transcribe_todo_with_all_fields(client):
    """A transcript with date and priority gets all fields populated."""
    mock_fields = {
        'title': 'Buy milk',
        'notes': 'at the grocery store',
        'priority': 2,
        'due': '2025-01-15',
    }
    with patch('backend.routes.tasks.extract_todo_fields', return_value=mock_fields):
        r = client.post('/api/tasks/todos/transcribe', json={'text': 'Buy milk at the store tomorrow'})
        assert r.status_code == 201
        data = r.get_json()
        assert 'id' in data

    # Verify the todo was created with the correct fields
    r = client.get('/api/tasks/todos')
    todos = r.get_json()
    todo = next(t for t in todos if t['title'] == 'Buy milk')
    assert todo['notes'] == 'at the grocery store'
    assert todo['priority'] == 2
    # due should be a unix timestamp for 2025-01-15 noon UTC
    assert todo['due'] is not None


def test_transcribe_todo_partial_fields(client):
    """A transcript with only title and priority (no due date) works."""
    mock_fields = {
        'title': 'Call dentist',
        'notes': None,
        'priority': 1,
        'due': None,
    }
    with patch('backend.routes.tasks.extract_todo_fields', return_value=mock_fields):
        r = client.post('/api/tasks/todos/transcribe', json={'text': 'Call the dentist'})
        assert r.status_code == 201

    r = client.get('/api/tasks/todos')
    todos = r.get_json()
    todo = next(t for t in todos if t['title'] == 'Call dentist')
    assert todo['priority'] == 1
    assert todo['notes'] is None
    assert todo['due'] is None


def test_transcribe_todo_title_truncation(client):
    """A very long transcript gets its title truncated to 100 chars."""
    long_text = 'x' * 200
    with patch('backend.routes.tasks.extract_todo_fields', return_value=None):
        r = client.post('/api/tasks/todos/transcribe', json={'text': long_text})
        assert r.status_code == 201

    r = client.get('/api/tasks/todos')
    todos = r.get_json()
    todo = next(t for t in todos if t['title'] == 'x' * 100)
    assert len(todo['title']) == 100


def test_transcribe_todo_invalid_date_falls_back(client):
    """An invalid date from the LLM is dropped, not fatal."""
    mock_fields = {
        'title': 'Fix the bike',
        'notes': None,
        'priority': None,
        'due': 'not-a-date',
    }
    with patch('backend.routes.tasks.extract_todo_fields', return_value=mock_fields):
        r = client.post('/api/tasks/todos/transcribe', json={'text': 'Fix the bike'})
        assert r.status_code == 201

    r = client.get('/api/tasks/todos')
    todos = r.get_json()
    todo = next(t for t in todos if t['title'] == 'Fix the bike')
    assert todo['due'] is None
