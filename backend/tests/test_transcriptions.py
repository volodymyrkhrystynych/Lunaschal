"""Tests for the transcription log: capture via /api/transcribe + list/delete routes."""
import io
import time

import pytest

from backend.db.connection import get_db
from backend.routes import stt
from ulid import ULID


@pytest.fixture
def mock_stt(monkeypatch):
    monkeypatch.setattr(stt, '_load_stt', lambda *a, **k: None)
    monkeypatch.setattr(
        stt, '_do_transcribe',
        lambda content, filename, language: {'text': 'hello world', 'language': 'en'},
    )


def _post_audio(client, source=None, **fields):
    data = {'audio': (io.BytesIO(b'\x00' * 2000), 'rec.wav'), **fields}
    if source is not None:
        data['source'] = source
    return client.post('/api/transcribe', data=data, content_type='multipart/form-data')


def _rows(client):
    return get_db().execute('SELECT * FROM transcriptions').fetchall()


def _insert(text, source='paste', created_at=None):
    db = get_db()
    db.execute(
        'INSERT INTO transcriptions (id, text, source, created_at) VALUES (?, ?, ?, ?)',
        (str(ULID()), text, source, created_at or int(time.time())),
    )
    db.commit()


def test_paste_source_is_logged(client, mock_stt):
    resp = _post_audio(client, source='paste')
    assert resp.status_code == 200
    assert resp.get_json()['text'] == 'hello world'
    rows = _rows(client)
    assert len(rows) == 1
    assert rows[0]['text'] == 'hello world'
    assert rows[0]['source'] == 'paste'


def test_no_source_is_not_logged(client, mock_stt):
    # The in-app editor SttPanel sends no source field
    resp = _post_audio(client)
    assert resp.status_code == 200
    assert _rows(client) == []


@pytest.mark.parametrize('source', ['journal', 'voice', 'command'])
def test_other_sources_are_not_logged(client, mock_stt, source):
    resp = _post_audio(client, source=source)
    assert resp.status_code == 200
    assert _rows(client) == []


def test_empty_transcription_is_not_logged(client, monkeypatch):
    monkeypatch.setattr(stt, '_load_stt', lambda *a, **k: None)
    monkeypatch.setattr(
        stt, '_do_transcribe',
        lambda content, filename, language: {'text': '', 'language': 'en'},
    )
    resp = _post_audio(client, source='paste')
    assert resp.status_code == 200
    assert _rows(client) == []


def test_window_context_app_only_for_non_browser(client, mock_stt):
    _post_audio(client, source='paste', app='kitty', window_title='secret document — vim')
    row = _rows(client)[0]
    assert row['app'] == 'kitty'
    assert row['detail'] is None  # titles outside browsers are not stored


def test_window_context_browser_stores_stripped_title(client, mock_stt):
    _post_audio(client, source='paste', app='firefox',
                window_title='Some Page Name — Mozilla Firefox')
    row = _rows(client)[0]
    assert row['app'] == 'firefox'
    assert row['detail'] == 'Some Page Name'


def test_window_context_browser_class_variants(client, mock_stt):
    _post_audio(client, source='paste', app='vivaldi-stable',
                window_title='Indeed Messages - Vivaldi')
    row = _rows(client)[0]
    assert row['app'] == 'vivaldi-stable'
    assert row['detail'] == 'Indeed Messages'


def test_window_context_absent(client, mock_stt):
    # hyprctl failed listener-side: no app/window_title fields sent
    _post_audio(client, source='paste')
    row = _rows(client)[0]
    assert row['app'] is None
    assert row['detail'] is None


def test_logged_transcription_does_not_appear_in_journal(client, mock_stt):
    _post_audio(client, source='paste')
    entries = client.get('/api/journal').get_json()
    assert entries == []
    fts = get_db().execute(
        "SELECT * FROM journal_fts WHERE journal_fts MATCH 'hello'"
    ).fetchall()
    assert fts == []


def test_list_transcriptions(client):
    _insert('oldest', created_at=1000)
    _insert('middle', created_at=2000)
    _insert('newest', created_at=3000)
    resp = client.get('/api/transcriptions')
    assert resp.status_code == 200
    data = resp.get_json()
    assert [t['text'] for t in data] == ['newest', 'middle', 'oldest']
    assert data[0]['source'] == 'paste'
    assert 'createdAt' in data[0] and 'T' in data[0]['createdAt']  # ISO-formatted


def test_list_limit_and_offset(client):
    for i in range(5):
        _insert(f't{i}', created_at=1000 + i)
    data = client.get('/api/transcriptions?limit=2&offset=1').get_json()
    assert [t['text'] for t in data] == ['t3', 't2']


def test_delete_transcription(client):
    _insert('to delete')
    tid = _rows(client)[0]['id']
    resp = client.delete(f'/api/transcriptions/{tid}')
    assert resp.status_code == 200
    assert resp.get_json() == {'success': True}
    assert _rows(client) == []
    # Idempotent: deleting again still succeeds
    assert client.delete(f'/api/transcriptions/{tid}').status_code == 200
