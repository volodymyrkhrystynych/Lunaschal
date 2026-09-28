"""Native/offline clients retain capture time across late uploads and retries."""
import io
from datetime import datetime

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.routes import journal


@pytest.fixture(autouse=True)
def isolated_capture(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    for name in ('_polish_bg', '_generate_metadata_bg', '_queue_attachment_transcription'):
        monkeypatch.setattr(journal, name, lambda *a, **k: None)


CAPTURED = '2026-09-20T01:30:00-04:00'
STAMP = int(datetime.fromisoformat(CAPTURED).timestamp())


def upload(client, entry_id, attachment_id, captured=CAPTURED, transcribe=False):
    return client.post('/api/journal/recordings', data={
        'id': entry_id, 'attachmentId': attachment_id,
        'capturedAt': captured, 'transcribe': str(transcribe).lower(),
        'file': (io.BytesIO(b'audio bytes'), 'capture.m4a', 'audio/mp4'),
    })


def test_offline_text_keeps_original_time_on_replay(client):
    entry_id = str(ULID())
    body = {'id': entry_id, 'content': 'Saved without a connection.', 'capturedAt': CAPTURED}
    assert client.post('/api/journal', json=body).status_code == 201
    assert client.post('/api/journal', json={**body, 'capturedAt': '2026-09-21T12:00:00Z'}).status_code == 201
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (entry_id,)).fetchone()
    assert row['created_at'] == STAMP
    assert row['content'] == body['content']


@pytest.mark.parametrize('transcribe', [False, True])
def test_offline_audio_keeps_time_ids_and_mode(client, monkeypatch, transcribe):
    calls = []
    monkeypatch.setattr(journal, '_queue_attachment_transcription', lambda *a, **k: calls.append(k))
    entry_id, attachment_id = str(ULID()), str(ULID())
    response = upload(client, entry_id, attachment_id, transcribe=transcribe)
    assert response.status_code == 201
    assert response.json['id'] == entry_id
    assert response.json['attachment']['id'] == attachment_id
    assert len(calls) == int(transcribe)
    if transcribe:
        assert calls[0]['into_entry'] is True
    assert upload(client, entry_id, attachment_id, '2026-09-22T10:00:00Z', transcribe).status_code == 201
    db = get_db()
    assert db.execute('SELECT created_at FROM journal_entries WHERE id=?', (entry_id,)).fetchone()[0] == STAMP
    assert db.execute('SELECT created_at FROM journal_attachments WHERE id=?', (attachment_id,)).fetchone()[0] == STAMP
    assert db.execute('SELECT COUNT(*) FROM journal_attachments WHERE entry_id=?', (entry_id,)).fetchone()[0] == 1


@pytest.mark.parametrize('value', [None, '', 12, True, {}, 'yesterday', '2026-09-20T01:30:00'])
def test_invalid_text_capture_time_is_refused_without_saving(client, value):
    entry_id = str(ULID())
    result = client.post('/api/journal', json={'id': entry_id, 'content': 'note', 'capturedAt': value})
    assert result.status_code == 400
    assert get_db().execute('SELECT id FROM journal_entries WHERE id=?', (entry_id,)).fetchone() is None


@pytest.mark.parametrize('value', ['', 'yesterday', '2026-09-20T01:30:00'])
def test_invalid_audio_capture_time_leaves_no_entry(client, value):
    entry_id = str(ULID())
    assert upload(client, entry_id, str(ULID()), value).status_code == 400
    assert get_db().execute('SELECT id FROM journal_entries WHERE id=?', (entry_id,)).fetchone() is None


def test_legacy_client_without_capture_time_still_works(client):
    assert client.post('/api/journal', json={'content': 'Current browser'}).status_code == 201
