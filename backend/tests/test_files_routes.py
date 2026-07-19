"""Parametrized CRUD + path-traversal coverage shared by the Files tab
(/api/files) and the Notebook tab (/api/notebook/files) mounts, both built
from backend.routes.files.make_files_blueprint.
"""
import pytest


@pytest.mark.parametrize('prefix,root_env', [
    ('/api/files', 'FILES_ROOT'),
    ('/api/notebook/files', 'NOTEBOOK_ROOT'),
])
class TestFilesCRUD:
    @pytest.fixture(autouse=True)
    def _root(self, monkeypatch, tmp_path, root_env):
        monkeypatch.setenv(root_env, str(tmp_path / 'root'))

    def test_write_read_list(self, client, prefix):
        r = client.post(f'{prefix}/write', json={'path': 'note.md', 'content': 'hello'})
        assert r.status_code == 200

        r = client.get(f'{prefix}/read', query_string={'path': 'note.md'})
        assert r.status_code == 200
        assert r.json['content'] == 'hello'

        r = client.get(prefix)
        assert r.status_code == 200
        names = {e['name'] for e in r.json}
        assert 'note.md' in names
        entry = next(e for e in r.json if e['name'] == 'note.md')
        assert entry['isDir'] is False
        assert entry['path'] == 'note.md'

    def test_write_creates_parent_dirs(self, client, prefix):
        r = client.post(f'{prefix}/write', json={'path': 'a/b/c.md', 'content': 'x'})
        assert r.status_code == 200
        r = client.get(prefix, query_string={'path': 'a/b'})
        assert r.status_code == 200
        assert any(e['name'] == 'c.md' for e in r.json)

    def test_path_traversal_rejected_on_read(self, client, prefix):
        r = client.get(f'{prefix}/read', query_string={'path': '../../etc/passwd'})
        assert r.status_code == 400

    def test_path_traversal_rejected_on_write(self, client, prefix):
        r = client.post(f'{prefix}/write', json={'path': '../escape.md', 'content': 'x'})
        assert r.status_code == 400

    def test_path_traversal_rejected_on_rename(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'a.md', 'content': 'x'})
        r = client.post(f'{prefix}/rename', json={'from': 'a.md', 'to': '../escape.md'})
        assert r.status_code == 400

    def test_mkdir_nested(self, client, prefix):
        r = client.post(f'{prefix}/mkdir', json={'path': 'folder/sub'})
        assert r.status_code == 200
        r = client.get(prefix)
        assert any(e['name'] == 'folder' and e['isDir'] for e in r.json)

    def test_rename_file(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'old.md', 'content': 'x'})
        r = client.post(f'{prefix}/rename', json={'from': 'old.md', 'to': 'new.md'})
        assert r.status_code == 200
        assert client.get(f'{prefix}/read', query_string={'path': 'old.md'}).status_code == 404
        assert client.get(f'{prefix}/read', query_string={'path': 'new.md'}).json['content'] == 'x'

    def test_rename_directory_moves_subtree(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'dir/child.md', 'content': 'x'})
        r = client.post(f'{prefix}/rename', json={'from': 'dir', 'to': 'dir2'})
        assert r.status_code == 200
        assert client.get(f'{prefix}/read', query_string={'path': 'dir2/child.md'}).json['content'] == 'x'

    def test_delete_moves_to_trash(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'gone.md', 'content': 'x'})
        r = client.delete(prefix, query_string={'path': 'gone.md'})
        assert r.status_code == 200
        assert client.get(f'{prefix}/read', query_string={'path': 'gone.md'}).status_code == 404
        assert client.get(f'{prefix}/read', query_string={'path': '.trash/gone.md'}).json['content'] == 'x'

    def test_delete_dir_moves_whole_subtree(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'dir/child.md', 'content': 'x'})
        r = client.delete(prefix, query_string={'path': 'dir'})
        assert r.status_code == 200
        assert client.get(f'{prefix}/read', query_string={'path': '.trash/dir/child.md'}).json['content'] == 'x'

    def test_delete_not_found(self, client, prefix):
        r = client.delete(prefix, query_string={'path': 'missing.md'})
        assert r.status_code == 404
