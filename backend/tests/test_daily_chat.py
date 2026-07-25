"""Tests for the daily-chat model: 4am day boundary, find-or-create today's
conversation, the journal-conversations feed, and AI title generation/sweep."""
import json
from datetime import datetime

from backend.db import connection
from backend.chat_day import day_key_for

NOW = int(datetime(2026, 7, 14, 18, 0).timestamp())


# --- day_key_for (pure) ---

def test_before_4am_belongs_to_previous_day():
    ts = int(datetime(2026, 7, 25, 3, 59).timestamp())
    assert day_key_for(ts) == '2026-07-24'


def test_after_4am_belongs_to_same_day():
    ts = int(datetime(2026, 7, 25, 4, 1).timestamp())
    assert day_key_for(ts) == '2026-07-25'


def test_2am_belongs_to_prior_morning():
    ts = int(datetime(2026, 7, 25, 2, 0).timestamp())
    assert day_key_for(ts) == '2026-07-24'


# --- fixtures ---

def _insert_conv(id, day_key, writing=None, title=None):
    connection.get_db().execute(
        'INSERT INTO conversations(id, title, day_key, writing_project_id, created_at, updated_at) '
        'VALUES (?,?,?,?,?,?)',
        (id, title, day_key, writing, NOW, NOW),
    )


def _insert_msg(id, conv, role='user', content='hi'):
    connection.get_db().execute(
        'INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES (?,?,?,?,?)',
        (id, conv, role, content, NOW),
    )


# --- find-or-create today ---

def test_create_conversation_is_find_or_create(client):
    r1 = client.post('/api/chat/conversations', json={})
    assert r1.status_code == 201
    id1 = r1.get_json()['id']
    r2 = client.post('/api/chat/conversations', json={})
    assert r2.status_code == 200  # found the existing day's conversation
    assert r2.get_json()['id'] == id1
    # New conversations start untitled (the nightly job fills the title).
    row = connection.get_db().execute(
        'SELECT title, day_key FROM conversations WHERE id=?', (id1,)
    ).fetchone()
    assert row['title'] is None
    assert row['day_key'] == day_key_for()


def test_new_day_creates_a_new_conversation(client, monkeypatch):
    monkeypatch.setattr('backend.routes.chat.day_key_for', lambda ts=None: '2026-07-24')
    id1 = client.post('/api/chat/conversations', json={}).get_json()['id']
    monkeypatch.setattr('backend.routes.chat.day_key_for', lambda ts=None: '2026-07-25')
    id2 = client.post('/api/chat/conversations', json={}).get_json()['id']
    assert id1 != id2


def test_today_null_then_returns_conversation(client):
    assert client.get('/api/chat/today').get_json() is None
    id1 = client.post('/api/chat/conversations', json={}).get_json()['id']
    today = client.get('/api/chat/today').get_json()
    assert today['id'] == id1
    assert today['messages'] == []


# --- journal-conversations ---

def test_journal_conversations_filters_and_shape(client):
    db = connection.get_db()
    db.execute(
        'INSERT INTO writing_projects(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        ('wp1', 'A story', NOW, NOW),
    )
    # Current live day — excluded.
    _insert_conv('c_today', day_key_for())
    _insert_msg('m0', 'c_today')
    # Two past days — included, newest day first.
    _insert_conv('c_old', '2026-01-01')
    _insert_msg('m1', 'c_old')
    _insert_msg('m2', 'c_old', 'assistant', 'yo')
    _insert_conv('c_newer', '2026-02-01')
    _insert_msg('m3', 'c_newer')
    # Writing-project chat — excluded.
    _insert_conv('c_writing', '2026-01-01', writing='wp1')
    _insert_msg('m4', 'c_writing')
    # Message-less past day — excluded (never actually chatted).
    _insert_conv('c_empty', '2026-01-01')
    # Past day with only a break marker (no real turns) — excluded.
    _insert_conv('c_break_only', '2026-01-01')
    _insert_msg('m5', 'c_break_only', 'system', '')
    db.commit()

    rows = client.get('/api/chat/journal-conversations').get_json()
    ids = [r['id'] for r in rows]
    # excludes today + writing + empty + break-only, day_key DESC
    assert ids == ['c_newer', 'c_old']
    old = next(r for r in rows if r['id'] == 'c_old')
    assert old['messageCount'] == 2
    assert old['dayKey'] == '2026-01-01'


# --- title generation ---

class _FakeCompletions:
    def create(self, **kwargs):
        payload = json.dumps({'title': 'My Great Title'})
        return type('R', (), {
            'choices': [type('C', (), {
                'message': type('M', (), {'content': payload})()
            })()]
        })()


class _FakeClient:
    chat = type('Chat', (), {'completions': _FakeCompletions()})()


def _mock_llm(monkeypatch):
    monkeypatch.setattr('backend.ai.chat_title.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.ai.chat_title.get_provider_config', lambda: {'ollama_model': 'm'})
    monkeypatch.setattr('backend.ai.chat_title.get_ollama_client', lambda c: _FakeClient())


def test_generate_conversation_title(monkeypatch):
    _mock_llm(monkeypatch)
    from backend.ai.chat_title import generate_conversation_title
    title = generate_conversation_title([
        {'role': 'user', 'content': 'How do I bake bread?'},
        {'role': 'assistant', 'content': 'Mix flour, water, salt, yeast...'},
        {'role': 'system', 'content': '', 'metadata': '{"break": true}'},  # skipped
    ])
    assert title == 'My Great Title'


def test_generate_title_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr('backend.ai.chat_title.is_ai_configured', lambda: False)
    from backend.ai.chat_title import generate_conversation_title
    assert generate_conversation_title([{'role': 'user', 'content': 'hi'}]) is None


def test_generate_title_none_when_no_real_messages(monkeypatch):
    _mock_llm(monkeypatch)
    from backend.ai.chat_title import generate_conversation_title
    assert generate_conversation_title([]) is None
    assert generate_conversation_title(
        [{'role': 'system', 'content': '', 'metadata': '{"break": true}'}]
    ) is None


def test_generate_title_route(client, monkeypatch):
    monkeypatch.setattr('backend.routes.chat.generate_conversation_title', lambda msgs: 'Fixed Title')
    _insert_conv('c1', '2026-01-01')
    _insert_msg('m1', 'c1')
    connection.get_db().commit()
    resp = client.post('/api/chat/conversations/c1/generate-title')
    assert resp.get_json()['title'] == 'Fixed Title'
    row = connection.get_db().execute('SELECT title FROM conversations WHERE id=?', ('c1',)).fetchone()
    assert row['title'] == 'Fixed Title'


# --- nightly sweep ---

def test_run_title_sweep(client, monkeypatch):
    monkeypatch.setattr(
        'backend.chat_title_scheduler.generate_conversation_title',
        lambda msgs: 'Swept Title',
    )
    _insert_conv('c_untitled', '2026-01-01')
    _insert_msg('m1', 'c_untitled')
    _insert_conv('c_titled', '2026-01-02', title='Already')
    _insert_msg('m2', 'c_titled')
    _insert_conv('c_empty', '2026-01-03')  # dated but no messages
    connection.get_db().commit()

    from backend.chat_title_scheduler import run_title_sweep
    assert run_title_sweep() == 1  # only the untitled-with-messages one

    db = connection.get_db()
    assert db.execute('SELECT title FROM conversations WHERE id=?', ('c_untitled',)).fetchone()['title'] == 'Swept Title'
    assert db.execute('SELECT title FROM conversations WHERE id=?', ('c_titled',)).fetchone()['title'] == 'Already'
    assert db.execute('SELECT title FROM conversations WHERE id=?', ('c_empty',)).fetchone()['title'] is None
