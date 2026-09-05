"""Path translation and the traversal guard.

The file-serving route hands whatever qBittorrent reported to `send_file`, so
`resolve_download_path` is the boundary between the client's namespace and the
host filesystem.
"""
import pytest

from backend.torrent import storage


@pytest.fixture(autouse=True)
def root(monkeypatch, tmp_path):
    downloads = tmp_path / 'torrents'
    downloads.mkdir()
    monkeypatch.setenv('TORRENT_ROOT', str(downloads))
    return downloads.resolve()


def test_container_path_maps_onto_the_host_root(root):
    assert storage.to_host_path('/downloads/Foo/bar.mkv') == root / 'Foo' / 'bar.mkv'


def test_bare_root_maps_to_the_root(root):
    assert storage.to_host_path('/downloads') == root


def test_a_path_outside_the_bind_mount_is_refused():
    assert storage.to_host_path('/etc/passwd') is None
    assert storage.to_host_path('/downloadsomething/x') is None


def test_host_to_container_round_trips(root):
    assert storage.to_container_path(root / 'Foo' / 'bar.mkv') == '/downloads/Foo/bar.mkv'


def test_host_path_outside_the_root_has_no_container_name(tmp_path):
    assert storage.to_container_path(tmp_path / 'elsewhere') is None


def test_resolve_accepts_a_file_inside_the_root(root):
    (root / 'Foo').mkdir()
    (root / 'Foo' / 'bar.mkv').write_bytes(b'x')
    assert storage.resolve_download_path('/downloads/Foo/bar.mkv') == root / 'Foo' / 'bar.mkv'


def test_resolve_accepts_a_file_that_does_not_exist_yet(root):
    """A partial download is a 404 from the route, not a traversal attempt —
    existence is the caller's business."""
    assert storage.resolve_download_path('/downloads/Foo/partial.mkv') is not None


def test_resolve_rejects_dot_dot_escape():
    assert storage.resolve_download_path('/downloads/../../etc/passwd') is None


def test_resolve_rejects_a_symlink_pointing_out_of_the_root(root, tmp_path):
    """The reason resolve() runs before the check rather than after: a torrent
    can ship a symlink, and a string comparison would pass it."""
    secret = tmp_path / 'secret.txt'
    secret.write_text('nope')
    (root / 'escape').symlink_to(secret)
    assert storage.resolve_download_path('/downloads/escape') is None


@pytest.mark.parametrize('char', list('"*/:<>?\\|'))
def test_every_exfat_forbidden_character_is_replaced(char):
    assert char not in storage.sanitize_for_exfat(f'Show{char}Name')


def test_sanitize_keeps_distinct_names_distinct():
    """Collapsing rather than dropping: two names differing only in punctuation
    must not become one directory."""
    assert storage.sanitize_for_exfat('A:B') != storage.sanitize_for_exfat('AB')


def test_sanitize_strips_trailing_dots_and_spaces():
    assert storage.sanitize_for_exfat('Name. ') == 'Name'


def test_sanitize_never_returns_empty():
    assert storage.sanitize_for_exfat('...') == 'untitled'


def test_sanitize_drops_control_characters():
    assert storage.sanitize_for_exfat('a\x00b\x1fc') == 'abc'
