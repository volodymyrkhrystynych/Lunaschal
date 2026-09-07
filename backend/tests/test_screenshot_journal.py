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
from backend.db.connection import _ensure_stt_shortcuts, get_db


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
    assert len(calls) == 2
    entry = calls[0].kwargs['json']
    assert entry['pendingAttachments'] == 1
    assert calls[1].kwargs['data']['attachmentId'] == entry['id']
    assert calls[1].kwargs['files']['file'][2] == 'image/png'
    assert not list(capture.root.iterdir())


def test_offline_capture_survives_restart_and_reuses_id(capture):
    capture.session.post.side_effect = ConnectionError('offline')
    capture.capture()
    path, = capture.root.glob('*.png')
    assert path.stat().st_mode & 0o777 == 0o600
    session = Mock()
    screenshots.ScreenshotJournal(session, 'http://test', capture.root).flush()
    assert session.post.call_args_list[0].kwargs['json']['id'] == path.stem
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
        if 'files' in kwargs:
            name, file, mime = kwargs['files']['file']
            response = client.post(route, data={**kwargs['data'],
                'file': (io.BytesIO(file.read()), name, mime)})
            assert response.status_code in (200, 201), response.get_json()
            if lose_response:
                lose_response = False
                raise ConnectionError('response lost after server saved it')
        else:
            response = client.post(route, json=kwargs['json'])
            assert response.status_code == 201
        return Mock()

    capture.session.post.side_effect = post
    capture.capture()
    assert len(list(capture.root.glob('*.png'))) == 1
    capture.flush()
    assert not list(capture.root.glob('*.png'))
    assert get_db().execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] == 1
    assert get_db().execute('SELECT COUNT(*) FROM journal_attachments').fetchone()[0] == 1


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
