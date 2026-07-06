"""Route tests for the voice-command endpoint (`backend/routes/voice_command.py`).

The LLM parse step is mocked (`parse_voice_command`) so these tests cover the
handler's own logic: request validation, action dispatch, the regex guards in
`_create_event`, persistence, and the clarify/none passthroughs — with no
network calls.
"""
import pytest

from backend.db.connection import get_db
from backend.routes import voice_command


def _mock_parser(monkeypatch, result):
    monkeypatch.setattr(voice_command, 'parse_voice_command', lambda messages: result)


def test_missing_user_turn_is_rejected(client):
    assert client.post('/api/voice-command', json={'messages': []}).status_code == 400
    # Only an assistant turn, no user content → still a 400.
    only_assistant = {'messages': [{'role': 'assistant', 'content': 'hi'}]}
    assert client.post('/api/voice-command', json=only_assistant).status_code == 400


def test_create_todo_action_persists(client, monkeypatch):
    _mock_parser(monkeypatch, {
        'action': 'create_todo', 'speak': 'Added a todo: buy milk.',
        'todo': {'title': 'buy milk'},
    })
    r = client.post('/api/voice-command', json={'messages': [{'role': 'user', 'content': 'remind me to buy milk'}]})
    data = r.get_json()
    assert data['status'] == 'done'
    assert data['action'] == 'create_todo'
    assert data['id']

    titles = [t['title'] for t in client.get('/api/tasks/todos').get_json()]
    assert titles == ['buy milk']


def test_create_event_persists_with_optional_time(client, monkeypatch):
    _mock_parser(monkeypatch, {
        'action': 'create_event', 'speak': 'Added the event.',
        'event': {'title': 'Dentist', 'date': '2026-08-11', 'time': '09:30'},
    })
    r = client.post('/api/voice-command', json={'messages': [{'role': 'user', 'content': 'dentist on the 11th'}]})
    assert r.get_json()['status'] == 'done'

    row = get_db().execute('SELECT title, date, time FROM calendar_events').fetchone()
    assert (row['title'], row['date'], row['time']) == ('Dentist', '2026-08-11', '09:30')


def test_invalid_event_date_creates_nothing(client, monkeypatch):
    # The LLM is instructed to emit YYYY-MM-DD; the route's _DATE_RE guard must
    # reject anything else rather than writing a garbage row.
    _mock_parser(monkeypatch, {
        'action': 'create_event', 'speak': 'Added the event.',
        'event': {'title': 'Whenever', 'date': 'next week'},
    })
    r = client.post('/api/voice-command', json={'messages': [{'role': 'user', 'content': 'meeting next week'}]})
    assert r.get_json()['status'] == 'none'
    assert get_db().execute('SELECT COUNT(*) c FROM calendar_events').fetchone()['c'] == 0


def test_bad_event_time_is_dropped_but_event_still_created(client, monkeypatch):
    _mock_parser(monkeypatch, {
        'action': 'create_event', 'speak': 'Added the event.',
        'event': {'title': 'Lunch', 'date': '2026-08-11', 'time': 'noonish'},
    })
    client.post('/api/voice-command', json={'messages': [{'role': 'user', 'content': 'lunch'}]})
    row = get_db().execute('SELECT time FROM calendar_events').fetchone()
    assert row['time'] is None


def test_clarify_is_passed_through(client, monkeypatch):
    _mock_parser(monkeypatch, {'action': 'clarify', 'speak': 'What day is the meeting?'})
    r = client.post('/api/voice-command', json={'messages': [{'role': 'user', 'content': 'schedule a meeting'}]})
    data = r.get_json()
    assert data['status'] == 'clarify'
    assert data['speak'] == 'What day is the meeting?'
    # A clarify must not create anything.
    assert get_db().execute('SELECT COUNT(*) c FROM calendar_events').fetchone()['c'] == 0


def test_create_journal_action_persists(client, monkeypatch):
    # The journal path kicks off background polish/embedding threads; stub them
    # so the test stays offline and deterministic.
    from backend.routes import journal
    for name in ('_notify_subscribers', '_sync_embeddings_bg', '_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal, name, lambda *a, **k: None)

    _mock_parser(monkeypatch, {
        'action': 'create_journal', 'speak': 'Saved it.',
        'journal': {'content': 'Today was a good day.'},
    })
    r = client.post('/api/voice-command', json={'messages': [{'role': 'user', 'content': 'journal that today was good'}]})
    assert r.get_json()['status'] == 'done'

    row = get_db().execute('SELECT content, raw_content FROM journal_entries').fetchone()
    assert row['content'] == 'Today was a good day.'
    assert row['raw_content'] == 'Today was a good day.'
