"""backend/routes/files.py: the /upload, /content, and /config routes that
exist only on the `files` (Files tab) mount, not on Notebook's — plus the
`_files_root()` precedence between Settings, FILES_ROOT, and the default.
"""

import io

import pytest

from backend.db.connection import get_db
from backend.files_config import set_config


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setenv('FILES_ROOT', str(tmp_path / 'root'))


def _upload(client, *, path='', files, relative_paths=None):
    data = {'path': path}
    data['file'] = [(io.BytesIO(content), name) for name, content in files]
    if relative_paths is not None:
        data['relative_path'] = relative_paths
    return client.post(
        '/api/files/upload', data=data, content_type='multipart/form-data'
    )


class TestUpload:
    def test_upload_writes_the_file(self, client):
        r = _upload(client, files=[('photo.jpg', b'\xff\xd8binary')])
        assert r.status_code == 200
        assert r.json['errors'] == []
        assert r.json['uploaded'] == [
            {'name': 'photo.jpg', 'path': 'photo.jpg', 'size': 8}
        ]
        assert client.get('/api/files/read', query_string={'path': 'photo.jpg'}).status_code == 422

    def test_upload_into_a_subfolder(self, client):
        r = _upload(client, path='pics', files=[('a.jpg', b'x')])
        assert r.status_code == 200
        assert r.json['uploaded'][0]['path'] == 'pics/a.jpg'
        entries = client.get('/api/files', query_string={'path': 'pics'}).json
        assert any(e['name'] == 'a.jpg' for e in entries)

    def test_upload_folder_preserves_its_relative_structure(self, client):
        r = _upload(
            client,
            files=[('one.txt', b'one'), ('two.txt', b'two')],
            relative_paths=['project/one.txt', 'project/nested/two.txt'],
        )
        assert r.status_code == 200
        assert r.json['errors'] == []
        assert {item['path'] for item in r.json['uploaded']} == {
            'project/one.txt',
            'project/nested/two.txt',
        }

    @pytest.mark.parametrize('relative_path', ['../escape.txt', '/escape.txt', r'..\\escape.txt'])
    def test_upload_folder_rejects_unsafe_relative_paths(self, client, relative_path):
        r = _upload(
            client,
            files=[('escape.txt', b'x')],
            relative_paths=[relative_path],
        )
        assert r.status_code == 200
        assert r.json['uploaded'] == []
        assert r.json['errors'][0]['error'] == 'Invalid relative path'

    def test_upload_rejects_traversal_in_destination_folder(self, client):
        r = _upload(client, path='../escape', files=[('a.jpg', b'x')])
        assert r.status_code == 400

    def test_upload_name_collision_auto_renames(self, client):
        _upload(client, files=[('dup.txt', b'first')])
        r = _upload(client, files=[('dup.txt', b'second')])
        assert r.status_code == 200
        assert r.json['uploaded'][0]['path'] == 'dup_1.txt'

        first = client.get('/api/files/content', query_string={'path': 'dup.txt'})
        second = client.get('/api/files/content', query_string={'path': 'dup_1.txt'})
        assert first.data == b'first'
        assert second.data == b'second'

    def test_upload_multiple_files_with_the_same_name_in_one_batch(self, client):
        r = _upload(client, files=[('dup.txt', b'first'), ('dup.txt', b'second')])
        assert r.status_code == 200
        paths = {u['path'] for u in r.json['uploaded']}
        assert paths == {'dup.txt', 'dup_1.txt'}

    def test_upload_with_no_files_returns_empty_lists(self, client):
        r = client.post(
            '/api/files/upload', data={'path': ''}, content_type='multipart/form-data'
        )
        assert r.status_code == 200
        assert r.json == {'uploaded': [], 'errors': []}


class TestContent:
    def test_content_serves_the_bytes_and_mimetype(self, client):
        _upload(client, files=[('note.txt', b'hello world')])
        r = client.get('/api/files/content', query_string={'path': 'note.txt'})
        assert r.status_code == 200
        assert r.data == b'hello world'
        assert r.mimetype == 'text/plain'
        assert 'attachment' not in (r.headers.get('Content-Disposition') or '')

    def test_content_download_sets_attachment_disposition(self, client):
        _upload(client, files=[('note.txt', b'hello')])
        r = client.get(
            '/api/files/content', query_string={'path': 'note.txt', 'download': '1'}
        )
        assert 'attachment' in r.headers.get('Content-Disposition', '')

    def test_content_rejects_traversal(self, client):
        r = client.get(
            '/api/files/content', query_string={'path': '../../etc/passwd'}
        )
        assert r.status_code == 400

    def test_content_404_on_missing_file(self, client):
        r = client.get('/api/files/content', query_string={'path': 'missing.bin'})
        assert r.status_code == 404

    def test_content_404_on_a_directory(self, client):
        client.post('/api/files/mkdir', json={'path': 'adir'})
        r = client.get('/api/files/content', query_string={'path': 'adir'})
        assert r.status_code == 404


class TestConfigRoutes:
    def test_get_config_default_unset(self, client):
        r = client.get('/api/files/config')
        assert r.status_code == 200
        assert r.json == {'path': '', 'source': 'unset'}

    def test_put_config_sets_the_path(self, client, tmp_path):
        dest = str(tmp_path / 'chosen')
        r = client.put('/api/files/config', json={'destination': dest})
        assert r.status_code == 200
        assert r.json == {'path': dest, 'source': 'settings'}
        assert client.get('/api/files/config').json['path'] == dest

    def test_put_config_rejects_a_relative_path(self, client):
        r = client.put('/api/files/config', json={'destination': 'relative/path'})
        assert r.status_code == 400
        assert 'error' in r.json


class TestRootPrecedence:
    def test_settings_wins_over_env_var(self, client, tmp_path):
        settings_root = tmp_path / 'from-settings'
        set_config(get_db(), path=str(settings_root))

        _upload(client, files=[('a.txt', b'x')])

        assert (settings_root / 'a.txt').exists()

    def test_env_var_used_when_settings_unset(self, client, tmp_path):
        env_root = tmp_path / 'root'

        _upload(client, files=[('a.txt', b'x')])

        assert (env_root / 'a.txt').exists()

    def test_falls_back_to_home_notes_when_neither_is_set(self, monkeypatch):
        from pathlib import Path

        monkeypatch.delenv('FILES_ROOT', raising=False)
        from backend.routes import files as files_module

        assert files_module._files_root() == (Path.home() / 'notes').resolve()
