"""backend/email/media.py — content-addressed image storage.

Every test points EMAIL_MEDIA_ROOT at a tmp_path, so nothing here can touch
the real store or the external drive.
"""
import pytest

from backend.email import media


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / 'media'
    root.mkdir()
    monkeypatch.setenv('EMAIL_MEDIA_ROOT', str(root))
    return root


def test_identical_bytes_are_stored_once(store_root):
    """The point of the whole design: a logo repeated across a mailbox is one
    file, no matter how many messages reference it."""
    data = b'\x89PNG\r\n\x1a\nfake-logo-bytes'
    first = media.store(data, 'image/png')
    second = media.store(data, 'image/png')

    assert first == second
    files = [p for p in store_root.rglob('*') if p.is_file()]
    assert len(files) == 1


def test_different_bytes_get_different_files(store_root):
    a, _, _ = media.store(b'aaaa', 'image/png')
    b, _, _ = media.store(b'bbbb', 'image/png')
    assert a != b
    assert len([p for p in store_root.rglob('*') if p.is_file()]) == 2


def test_same_image_from_two_urls_still_stores_once(store_root):
    """URL keying alone can't see through a rotated CDN hostname or a cache
    buster; content hashing is what collapses those."""
    data = b'identical-logo'
    assert media.url_hash('https://a.example/l.png') != media.url_hash('https://b.example/l.png')
    d1, _, _ = media.store(data, 'image/png')
    d2, _, _ = media.store(data, 'image/png')
    assert d1 == d2


def test_round_trip_read(store_root):
    data = b'some-bytes'
    digest, ext, size = media.store(data, 'image/jpeg')
    assert ext == 'jpg'
    assert size == len(data)
    assert media.read(digest, ext) == data


def test_read_of_absent_file_is_none(store_root):
    assert media.read('0' * 64, 'png') is None


def test_files_fan_out_into_subdirectories(store_root):
    """exFAT directory lookup degrades badly past tens of thousands of
    entries, and a large mailbox produces exactly that."""
    digest, ext, _ = media.store(b'x', 'image/png')
    expected = store_root / digest[:2] / digest[2:4] / f'{digest}.{ext}'
    assert expected.is_file()


def test_no_partial_file_is_left_behind(store_root):
    media.store(b'y', 'image/png')
    assert [p for p in store_root.rglob('*.part')] == []


def test_unknown_content_type_falls_back_to_bin(store_root):
    _, ext, _ = media.store(b'z', 'application/octet-stream')
    assert ext == 'bin'


def test_configured_root_that_does_not_exist_is_unavailable(tmp_path, monkeypatch):
    """The load-bearing one. An unmounted 7 TB disk is an empty directory (or
    no directory) on the system SSD — creating it and writing on would pour a
    mail archive into the root filesystem and look like it was working."""
    missing = tmp_path / 'not-mounted'
    monkeypatch.setenv('EMAIL_MEDIA_ROOT', str(missing))

    assert media.is_available() is False
    # And it must not have created it as a side effect of checking.
    assert not missing.exists()


def test_default_root_is_created_on_demand(tmp_path, monkeypatch):
    """Only the *explicitly configured* root is treated as a mountpoint. The
    default lives under ./data with everything else and is made as needed."""
    monkeypatch.delenv('EMAIL_MEDIA_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)

    assert media.is_available() is True
    assert (tmp_path / 'data' / 'email' / 'media').is_dir()
