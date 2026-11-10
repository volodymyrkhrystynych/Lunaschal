"""Tests for POST /api/tasks/todos/transcribe."""

from unittest.mock import patch

from backend.ai import todo as todo_ai


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
    # The cut title loses nothing: the whole transcript is kept as the notes.
    assert todo['notes'] == long_text


def test_transcribe_todo_short_fallback_has_no_notes(client):
    with patch('backend.routes.tasks.extract_todo_fields', return_value=None):
        client.post('/api/tasks/todos/transcribe', json={'text': 'Water plants'})
    todo = next(t for t in client.get('/api/tasks/todos').get_json() if t['title'] == 'Water plants')
    assert todo['notes'] is None


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


def test_replayed_id_creates_one_todo_and_skips_the_model(client):
    fields = {'title': 'Renew passport', 'notes': None, 'priority': None, 'due': None}
    with patch('backend.routes.tasks.extract_todo_fields', return_value=fields) as extract:
        r1 = client.post('/api/tasks/todos/transcribe', json={'text': 'renew passport', 'id': 'phone-1'})
        r2 = client.post('/api/tasks/todos/transcribe', json={'text': 'renew passport', 'id': 'phone-1'})
    assert (r1.status_code, r1.get_json()['id']) == (201, 'phone-1')
    assert (r2.status_code, r2.get_json()['id']) == (200, 'phone-1')
    assert extract.call_count == 1
    todos = client.get('/api/tasks/todos').get_json()
    assert [t['id'] for t in todos if t['title'] == 'Renew passport'] == ['phone-1']


def test_rejects_a_non_string_id(client):
    r = client.post('/api/tasks/todos/transcribe', json={'text': 'x', 'id': 5})
    assert r.status_code == 400


def test_route_passes_todays_4am_day(client):
    with patch('backend.routes.tasks.day_key_for', return_value='2026-11-09'), \
         patch('backend.routes.tasks.extract_todo_fields', return_value=None) as extract:
        client.post('/api/tasks/todos/transcribe', json={'text': 'call mum tomorrow'})
    extract.assert_called_once_with('call mum tomorrow', '2026-11-09')


def test_prompt_carries_today_and_weekday():
    with patch.object(todo_ai, 'chat_json', return_value={'title': 'Call mum'}) as chat:
        todo_ai.extract_todo_fields('call mum tomorrow', '2026-11-09')
    prompt = chat.call_args.args[0]
    assert 'Monday, 2026-11-09' in prompt
    assert 'call mum tomorrow' in prompt


def test_schema_bounds_priority():
    prio = todo_ai._TODO_SCHEMA['properties']['priority']
    assert (prio['minimum'], prio['maximum']) == (1, 5)


def test_out_of_range_priority_is_clamped_not_fatal(client):
    for raw, expected in ((0, 1), (10, 5), (True, None), ('high', None)):
        with patch.object(todo_ai, 'chat_json', return_value={'title': 't', 'priority': raw}):
            assert todo_ai.extract_todo_fields('t', '2026-11-09')['priority'] == expected

    with patch.object(todo_ai, 'chat_json', return_value={'title': 'Top job', 'priority': 0}):
        r = client.post('/api/tasks/todos/transcribe', json={'text': 'top priority top job'})
    assert r.status_code == 201
    todo = next(t for t in client.get('/api/tasks/todos').get_json() if t['title'] == 'Top job')
    assert todo['priority'] == 1


def test_model_failure_returns_none():
    with patch.object(todo_ai, 'chat_json', side_effect=RuntimeError('down')):
        assert todo_ai.extract_todo_fields('anything', '2026-11-09') is None
