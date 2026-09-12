"""Capture failures, offline recovery, and real journal replay without a display."""
import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from requests import ConnectionError

from stt import screenshots
from backend.db.connection import (
    _ensure_journal_screenshot_event_threshold,
    _ensure_stt_shortcuts,
    get_db,
)


@pytest.fixture
def capture(monkeypatch, tmp_path):
    buf = io.BytesIO()
    Image.new('RGB', (8, 8)).save(buf, 'PNG')
    monkeypatch.setattr(screenshots, 'notify', Mock())
    monkeypatch.setattr(screenshots.shutil, 'which', lambda _: '/usr/bin/grim')
    def run(args, **kwargs):
        if args[0] == 'grim':
            assert args[1:3] == ['-o', 'DP-2']
            Path(args[-1]).write_bytes(buf.getvalue())
        if args[0] == 'hyprctl':
            return Mock(stdout=json.dumps([
                {'name': 'DP-1', 'focused': False},
                {'name': 'DP-2', 'focused': True},
            ]), returncode=0)
        return Mock(stdout='', returncode=0)

    monkeypatch.setattr(screenshots.subprocess, 'run', run)
    return screenshots.ScreenshotJournal(Mock(), 'http://test', tmp_path / 'queue')


def test_capture_uploads_png_and_removes_confirmed_local_copy(capture):
    capture.capture()
    calls = capture.session.post.call_args_list
    assert len(calls) == 1
    assert calls[0].args[0].endswith('/api/journal/screenshots')
    assert calls[0].kwargs['data']['attachmentId']
    assert calls[0].kwargs['data']['capturedAt']
    assert calls[0].kwargs['files']['file'][2] == 'image/png'
    assert not list(capture.root.iterdir())


def test_offline_capture_survives_restart_and_reuses_id(capture):
    capture.session.post.side_effect = ConnectionError('offline')
    capture.capture()
    path, = capture.root.glob('*.png')
    assert path.stat().st_mode & 0o777 == 0o600
    session = Mock()
    screenshots.ScreenshotJournal(session, 'http://test', capture.root).flush()
    assert session.post.call_args_list[0].kwargs['data']['attachmentId'] == path.stem
    assert not path.exists()


def test_failed_capture_creates_no_entry(capture, monkeypatch):
    monkeypatch.setattr(screenshots.shutil, 'which', lambda _: None)
    capture.capture()
    capture.session.post.assert_not_called()
    assert not capture.root.exists()


def test_invalid_capture_cleans_partial_file(capture, monkeypatch):
    monkeypatch.setattr(screenshots, 'focused_monitor', lambda: 'DP-2')
    monkeypatch.setattr(screenshots.subprocess, 'run', lambda *a, **kw: None)
    capture.capture()
    capture.session.post.assert_not_called()
    assert not list(capture.root.iterdir())


@pytest.mark.parametrize('monitors', [[], [{'name': 'DP-1', 'focused': False}],
    [{'name': '', 'focused': True}],
    [{'name': 'DP-1', 'focused': True}, {'name': 'DP-2', 'focused': True}]])
def test_unknown_focus_never_captures_all_screens(capture, monkeypatch, monitors):
    run = Mock(return_value=Mock(stdout=json.dumps(monitors)))
    monkeypatch.setattr(screenshots.subprocess, 'run', run)
    capture.capture()
    assert run.call_count == 1
    assert run.call_args.args[0] == ['hyprctl', '-j', 'monitors']
    capture.session.post.assert_not_called()
    assert not capture.root.exists()


def test_focus_is_queried_again_for_each_capture(capture, monkeypatch):
    outputs = iter(['DP-1', 'DP-2'])
    grim_calls = []
    def run(args, **kwargs):
        if args[0] == 'hyprctl':
            return Mock(stdout=json.dumps([{'name': next(outputs), 'focused': True}]))
        grim_calls.append(args)
        Path(args[-1]).write_bytes(b'\x89PNG\r\n\x1a\n')
    monkeypatch.setattr(screenshots.subprocess, 'run', run)
    capture.capture()
    capture.capture()
    assert [args[2] for args in grim_calls] == ['DP-1', 'DP-2']


def test_lost_upload_response_replays_without_duplicate_entry_or_photo(
        capture, client, monkeypatch, tmp_path):
    from backend.routes import journal
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    monkeypatch.setattr(journal, '_generate_metadata_bg', lambda *a, **kw: None)
    lose_response = True

    def post(url, **kwargs):
        nonlocal lose_response
        route = url.removeprefix('http://test')
        name, file, mime = kwargs['files']['file']
        response = client.post(route, data={**kwargs['data'],
            'file': (io.BytesIO(file.read()), name, mime)})
        assert response.status_code == 201, response.get_json()
        if lose_response:
            lose_response = False
            raise ConnectionError('response lost after server saved it')
        return Mock()

    capture.session.post.side_effect = post
    capture.capture()
    assert len(list(capture.root.glob('*.png'))) == 1
    capture.flush()
    assert not list(capture.root.glob('*.png'))
    assert get_db().execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] == 1
    assert get_db().execute('SELECT COUNT(*) FROM journal_attachments').fetchone()[0] == 1
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0


def _png():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8)).save(buf, 'PNG')
    return buf.getvalue()


def _upload(client, attachment_id, captured_at):
    return client.post('/api/journal/screenshots', data={
        'attachmentId': attachment_id,
        'capturedAt': captured_at,
        'file': (io.BytesIO(_png()), 'screenshot.png', 'image/png'),
    })


def test_consecutive_screenshots_share_entry_and_extend_calendar_event(
        client, monkeypatch, tmp_path):
    from backend.routes import journal
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    monkeypatch.setattr(journal, '_transcribe_attachment_bg', lambda *a, **kw: None)

    first = _upload(client, '01J00000000000000000000001',
                    '2026-09-12T18:04:17-04:00')
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0
    last = _upload(client, '01J00000000000000000000002',
                   '2026-09-12T21:11:42-04:00')
    delayed = _upload(client, '01J0000000000000000000000A',
                      '2026-09-12T17:55:03-04:00')
    assert first.status_code == last.status_code == delayed.status_code == 201
    assert first.get_json()['id'] == last.get_json()['id'] == delayed.get_json()['id']

    entry_id = first.get_json()['id']
    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['title'] == 'Screenshots'
    assert entry['content'] == ''
    assert [a['name'] for a in entry['attachments']] == [
        '2026-09-12 17:55:03', '2026-09-12 18:04:17',
        '2026-09-12 21:11:42',
    ]

    event = get_db().execute(
        'SELECT * FROM calendar_events WHERE journal_id=?', (entry_id,)
    ).fetchone()
    assert (event['title'], event['date'], event['time'], event['end_time']) == (
        'Screenshots', '2026-09-12', '17:55', '21:11',
    )


def test_editing_screenshot_entry_starts_a_new_group(client, monkeypatch, tmp_path):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    first = _upload(client, '01J00000000000000000000003',
                    '2026-09-12T18:04:17-04:00').get_json()['id']
    assert client.patch(f'/api/journal/{first}', json={
        'content': 'Playing for the evening.',
    }).status_code == 200
    second = _upload(client, '01J00000000000000000000004',
                     '2026-09-12T18:30:00-04:00').get_json()['id']
    assert second != first


def test_another_journal_entry_starts_a_new_screenshot_group(
        client, monkeypatch, tmp_path):
    from backend.routes import journal
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    monkeypatch.setattr(journal, '_generate_metadata_bg', lambda *a, **kw: None)
    first = _upload(client, '01J00000000000000000000005',
                    '2026-09-12T18:04:17-04:00').get_json()['id']
    assert client.post('/api/journal', json={
        'content': 'A transcribed thought.', 'title': 'Thought',
    }).status_code == 201
    second = _upload(client, '01J00000000000000000000006',
                     '2026-09-12T18:30:00-04:00').get_json()['id']
    assert second != first
    assert get_db().execute(
        'SELECT is_open FROM journal_screenshot_sessions WHERE entry_id=?',
        (first,),
    ).fetchone()['is_open'] == 0


def test_transcribed_recording_starts_a_new_screenshot_group(
        client, monkeypatch, tmp_path, queued_jobs):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    first = _upload(client, '01J0000000000000000000000B',
                    '2026-09-12T18:04:17-04:00').get_json()['id']
    recording = client.post('/api/journal/recordings', data={
        'id': '01J0000000000000000000000C',
        'attachmentId': '01J0000000000000000000000D',
        'transcribe': 'true',
        'file': (io.BytesIO(b'audio bytes'), 'thought.wav', 'audio/wav'),
    })
    assert recording.status_code == 201
    second = _upload(client, '01J0000000000000000000000E',
                     '2026-09-12T18:30:00-04:00').get_json()['id']
    assert second != first


def test_screenshot_replay_is_idempotent(client, monkeypatch, tmp_path):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    attachment_id = '01J00000000000000000000007'
    first = _upload(client, attachment_id, '2026-09-12T18:04:17-04:00')
    replay = _upload(client, attachment_id, '2026-09-12T18:04:17-04:00')
    assert replay.status_code == 201
    assert replay.get_json()['id'] == first.get_json()['id']
    assert get_db().execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] == 1
    assert get_db().execute('SELECT COUNT(*) FROM journal_attachments').fetchone()[0] == 1
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0


def test_deleting_screenshot_entry_also_deletes_generated_event(
        client, monkeypatch, tmp_path):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    entry_id = _upload(client, '01J00000000000000000000008',
                       '2026-09-12T18:04:17-04:00').get_json()['id']
    _upload(client, '01J0000000000000000000000F',
            '2026-09-12T18:30:00-04:00')
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 1
    assert client.delete(f'/api/journal/{entry_id}').status_code == 200
    assert get_db().execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] == 0
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0


@pytest.mark.parametrize('captured_at', ['', '2026-09-12T18:04:17'])
def test_screenshot_requires_offset_local_capture_time(client, captured_at):
    response = _upload(client, '01J00000000000000000000009', captured_at)
    assert response.status_code == 400


def test_deleting_back_to_one_screenshot_removes_event(client, monkeypatch, tmp_path):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    first_attachment = '01J0000000000000000000000G'
    entry_id = _upload(client, first_attachment,
                       '2026-09-12T18:04:17-04:00').get_json()['id']
    second_attachment = '01J0000000000000000000000H'
    _upload(client, second_attachment, '2026-09-12T18:30:00-04:00')
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 1

    assert client.delete(
        f'/api/journal/attachments/{second_attachment}'
    ).status_code == 200
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0
    session = get_db().execute(
        'SELECT calendar_event_id, first_captured_at, last_captured_at'
        ' FROM journal_screenshot_sessions WHERE entry_id=?',
        (entry_id,),
    ).fetchone()
    assert session['calendar_event_id'] is None
    assert session['first_captured_at'] == session['last_captured_at']


def test_screenshot_shortcut_can_be_saved_and_disabled(client):
    for key in ('KEY_LEFTCTRL+KEY_F8', ''):
        response = client.patch('/api/settings/ai', json={'sttScreenshotKey': key})
        assert response.status_code == 200
        assert client.get('/api/settings').get_json()['sttScreenshotKey'] == key


def test_shortcut_migration_is_idempotent():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE settings(id INTEGER PRIMARY KEY)')
    _ensure_stt_shortcuts(db)
    _ensure_stt_shortcuts(db)
    assert 'stt_screenshot_key' in {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    db.close()


def test_screenshot_session_migration_allows_an_entry_without_an_event():
    db = sqlite3.connect(':memory:')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE journal_entries(id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE calendar_events(id TEXT PRIMARY KEY)')
    db.execute(
        '''CREATE TABLE journal_screenshot_sessions (
               entry_id TEXT PRIMARY KEY REFERENCES journal_entries(id) ON DELETE CASCADE,
               calendar_event_id TEXT NOT NULL UNIQUE
                   REFERENCES calendar_events(id) ON DELETE CASCADE,
               is_open INTEGER NOT NULL DEFAULT 1,
               first_captured_at INTEGER NOT NULL,
               last_captured_at INTEGER NOT NULL,
               first_local_date TEXT NOT NULL,
               first_local_time TEXT NOT NULL,
               last_local_date TEXT NOT NULL,
               last_local_time TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL
           )'''
    )
    db.execute("INSERT INTO journal_entries VALUES ('entry')")
    db.execute("INSERT INTO calendar_events VALUES ('event')")
    db.execute(
        "INSERT INTO journal_screenshot_sessions VALUES "
        "('entry','event',1,1,2,'2026-09-12','10:00:00',"
        "'2026-09-12','11:00:00',1,2)"
    )

    _ensure_journal_screenshot_event_threshold(db)
    assert db.execute(
        'SELECT entry_id, calendar_event_id FROM journal_screenshot_sessions'
    ).fetchone() == ('entry', 'event')
    event_column = next(
        row for row in db.execute('PRAGMA table_info(journal_screenshot_sessions)')
        if row[1] == 'calendar_event_id'
    )
    assert event_column[3] == 0
    event_fk = next(
        row for row in db.execute('PRAGMA foreign_key_list(journal_screenshot_sessions)')
        if row[3] == 'calendar_event_id'
    )
    assert event_fk[6] == 'SET NULL'
    db.close()
