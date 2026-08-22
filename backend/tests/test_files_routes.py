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

    def test_tree_walks_recursively(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'top.md', 'content': 'x'})
        client.post(f'{prefix}/write', json={'path': 'a/b/deep.md', 'content': 'x'})

        r = client.get(f'{prefix}/tree')
        assert r.status_code == 200
        assert r.json['truncated'] is False
        by_path = {e['path'] for e in r.json['entries']}
        # The single-level list endpoint would only ever return the first two.
        assert by_path == {'top.md', 'a', 'a/b', 'a/b/deep.md'}
        dirs = {e['path'] for e in r.json['entries'] if e['isDir']}
        assert dirs == {'a', 'a/b'}

    def test_tree_lists_a_parent_before_its_children(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'a/b/deep.md', 'content': 'x'})
        paths = [e['path'] for e in client.get(f'{prefix}/tree').json['entries']]
        assert paths.index('a') < paths.index('a/b') < paths.index('a/b/deep.md')

    def test_tree_hides_dotfiles_and_deleted_notes(self, client, prefix):
        client.post(f'{prefix}/write', json={'path': 'keep.md', 'content': 'x'})
        client.post(f'{prefix}/write', json={'path': 'gone.md', 'content': 'x'})
        client.post(f'{prefix}/write', json={'path': '.hidden/secret.md', 'content': 'x'})
        # delete_file moves into .trash/, which must not resurface as a note.
        assert client.delete(prefix, query_string={'path': 'gone.md'}).status_code == 200

        paths = {e['path'] for e in client.get(f'{prefix}/tree').json['entries']}
        assert paths == {'keep.md'}

    def test_tree_on_a_missing_root_creates_it_and_returns_empty(self, client, prefix):
        r = client.get(f'{prefix}/tree')
        assert r.status_code == 200
        assert r.json == {'entries': [], 'truncated': False}

    def test_tree_stops_at_the_entry_ceiling(self, client, prefix, monkeypatch):
        monkeypatch.setattr('backend.routes.files.MAX_TREE_ENTRIES', 3)
        for i in range(6):
            client.post(f'{prefix}/write', json={'path': f'n{i}.md', 'content': 'x'})

        r = client.get(f'{prefix}/tree')
        assert len(r.json['entries']) == 3
        assert r.json['truncated'] is True

    def test_tree_does_not_follow_a_symlinked_directory(self, client, prefix, tmp_path):
        client.post(f'{prefix}/write', json={'path': 'real/note.md', 'content': 'x'})
        root = tmp_path / 'root'
        # A loop: without the is_symlink() guard this recurses to the depth cap.
        (root / 'loop').symlink_to(root, target_is_directory=True)

        r = client.get(f'{prefix}/tree')
        assert r.json['truncated'] is False
        paths = {e['path'] for e in r.json['entries']}
        assert paths == {'real', 'real/note.md', 'loop'}
