"""Tests for the journal-aware chat system prompt.

`build_chat_system_prompt` should include journal entries from the last 24
hours — excluding fanfic-commentary entries (rows in journal_entry_fic_refs) —
and the /api/chat/stream route should only apply it when the caller did not
supply its own systemPrompt.
"""
import json
from datetime import datetime

from backend.db import connection
from backend.ai.chat import (
    SYSTEM_PROMPT,
    JOURNAL_MAX_CHARS,
    JOURNAL_MAX_ENTRIES,
    build_chat_system_prompt,
)

# Fixed "now" at 18:00 local time so today/yesterday labels are deterministic.
NOW = int(datetime(2026, 7, 14, 18, 0).timestamp())


def _insert_entry(id, content, created_at, title=None):
    connection.get_db().execute(
        'INSERT INTO journal_entries (id, content, title, created_at, updated_at) VALUES (?,?,?,?,?)',
        (id, content, title, created_at, created_at),
    )


def _insert_fic_ref(entry_id):
    db = connection.get_db()
    db.execute(
        "INSERT OR IGNORE INTO fics (id, title, source_type, created_at, updated_at) "
        "VALUES ('fic1', 'Some Fic', 'epub', ?, ?)",
        (NOW, NOW),
    )
    db.execute(
        'INSERT INTO journal_entry_fic_refs (id, journal_entry_id, fic_id, created_at) VALUES (?,?,?,?)',
        (f'ref-{entry_id}', entry_id, 'fic1', NOW),
    )


def test_recent_entries_included(client):
    _insert_entry('e1', 'Went for a long walk by the river.', NOW - 3600, title='Evening walk')
    prompt = build_chat_system_prompt(NOW)
    assert prompt.startswith(SYSTEM_PROMPT)
    assert 'Went for a long walk by the river.' in prompt
    assert 'Evening walk' in prompt


def test_old_entries_excluded(client):
    _insert_entry('e1', 'This one is too old.', NOW - 90000)
    assert build_chat_system_prompt(NOW) == SYSTEM_PROMPT


def test_fanfic_commentary_excluded(client):
    _insert_entry('e1', 'Commentary about chapter twelve.', NOW - 1800)
    _insert_entry('e2', 'A normal journal entry.', NOW - 1800)
    _insert_fic_ref('e1')
    prompt = build_chat_system_prompt(NOW)
    assert 'Commentary about chapter twelve.' not in prompt
    assert 'A normal journal entry.' in prompt


def test_entry_cap_and_order(client):
    for i in range(12):
        _insert_entry(f'e{i:02d}', f'Entry number {i:02d}.', NOW - 80000 + i * 1000)
    prompt = build_chat_system_prompt(NOW)
    # Only the newest 10 (02..11) appear.
    assert 'Entry number 00.' not in prompt
    assert 'Entry number 01.' not in prompt
    included = [i for i in range(12) if f'Entry number {i:02d}.' in prompt]
    assert included == list(range(2, 12))
    assert len(included) == JOURNAL_MAX_ENTRIES
    # Oldest first within the context block.
    assert prompt.index('Entry number 02.') < prompt.index('Entry number 11.')


def test_content_truncation(client):
    _insert_entry('e1', 'x' * (JOURNAL_MAX_CHARS + 500), NOW - 60)
    prompt = build_chat_system_prompt(NOW)
    assert 'x' * JOURNAL_MAX_CHARS + '…' in prompt
    assert 'x' * (JOURNAL_MAX_CHARS + 1) not in prompt


def test_timestamp_labels(client):
    _insert_entry('e1', 'Entry from earlier today.', NOW - 2 * 3600)   # 16:00 today
    _insert_entry('e2', 'Entry from last night.', NOW - 20 * 3600)     # 22:00 yesterday
    prompt = build_chat_system_prompt(NOW)
    assert '[today 16:00]' in prompt
    assert '[yesterday 22:00]' in prompt


# --- /api/chat/stream prompt assembly ---

def _capture_stream(monkeypatch):
    captured = {}

    def fake_chat_stream(messages, rag_context='', system_prompt=''):
        captured['messages'] = messages
        captured['rag_context'] = rag_context
        captured['system_prompt'] = system_prompt
        yield 'ok'

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.chat_stream', fake_chat_stream)
    return captured


def _post_stream(client, body):
    resp = client.post('/api/chat/stream', data=json.dumps(body), content_type='application/json')
    assert resp.status_code == 200
    resp.get_data()  # drain the SSE stream so the generator runs
    return resp


def test_stream_default_prompt_gets_journal_context(client, monkeypatch):
    _insert_entry('e1', 'Baked sourdough bread today.', int(datetime.now().timestamp()) - 600)
    captured = _capture_stream(monkeypatch)
    _post_stream(client, {'messages': [{'role': 'user', 'content': 'hi'}]})
    assert captured['system_prompt'].startswith(SYSTEM_PROMPT)
    assert 'Baked sourdough bread today.' in captured['system_prompt']


def test_stream_custom_prompt_untouched(client, monkeypatch):
    _insert_entry('e1', 'Baked sourdough bread today.', int(datetime.now().timestamp()) - 600)
    captured = _capture_stream(monkeypatch)
    custom = 'You are a creative writing partner.'
    _post_stream(client, {'messages': [], 'systemPrompt': custom})
    assert captured['system_prompt'] == custom
    assert 'Baked sourdough bread today.' not in captured['system_prompt']


def test_stream_passes_rag_context(client, monkeypatch):
    captured = _capture_stream(monkeypatch)
    _post_stream(client, {'messages': [], 'ragContext': 'some rag context'})
    assert captured['rag_context'] == 'some rag context'
