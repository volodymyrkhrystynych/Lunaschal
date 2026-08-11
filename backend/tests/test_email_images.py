"""backend/email/images.py — the low-priority image fetcher.

requests is monkeypatched throughout; nothing here touches the network, and
EMAIL_MEDIA_ROOT points at tmp_path so nothing touches the real store.
"""
import pytest

from backend.db.connection import get_db
from backend.email import images, media


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / 'media'
    root.mkdir()
    monkeypatch.setenv('EMAIL_MEDIA_ROOT', str(root))
    return root


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(images.time, 'sleep', lambda _s: None)


class _FakeResponse:
    def __init__(self, data=b'', content_type='image/png', status=200):
        self._data = data
        self.headers = {'Content-Type': content_type}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'{self.status_code} error')

    def iter_content(self, chunk_size):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]


def _queue(url: str) -> str:
    db = get_db()
    digest = media.url_hash(url)
    images.queue_images(db, [(digest, url)])
    return digest


def _row(url_hash: str):
    return get_db().execute(
        'SELECT * FROM email_images WHERE url_hash=?', (url_hash,)
    ).fetchone()


def test_fetches_and_stores_an_image(client, store_root, monkeypatch):
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: _FakeResponse(b'PNGDATA'))
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    digest = _queue('https://cdn.example/logo.png')

    assert images.fetch_pending() == 1

    row = _row(digest)
    assert row['status'] == 'stored'
    assert row['content_hash'] == media.content_hash(b'PNGDATA')
    assert media.read(row['content_hash'], row['extension']) == b'PNGDATA'


def test_two_urls_with_identical_bytes_share_one_file(client, store_root, monkeypatch):
    """The user's actual request: identical logos must not be stored twice
    even when they arrive from different URLs."""
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: _FakeResponse(b'SAMELOGO'))
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    a = _queue('https://a.example/logo.png')
    b = _queue('https://b.example/logo.png?v=2')

    images.fetch_pending()

    assert _row(a)['content_hash'] == _row(b)['content_hash']
    assert len([p for p in store_root.rglob('*') if p.is_file()]) == 1


def test_a_repeated_url_is_only_fetched_once(client, store_root, monkeypatch):
    calls = []

    def _get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(b'X')

    monkeypatch.setattr(images.requests, 'get', _get)
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    url = 'https://cdn.example/logo.png'
    _queue(url)
    _queue(url)
    _queue(url)

    images.fetch_pending()
    images.fetch_pending()

    assert len(calls) == 1


def test_private_address_is_skipped_and_never_fetched(client, store_root, monkeypatch):
    """Image URLs come from email, so they are hostile input: without the
    SSRF guard a crafted <img src> would have the server fetch its own
    localhost API or a cloud metadata endpoint."""
    calls = []
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: calls.append(a) or _FakeResponse())
    digest = _queue('http://127.0.0.1:5000/api/settings')

    images.fetch_pending()

    assert calls == []
    row = _row(digest)
    assert row['status'] == 'skipped'
    assert 'local' in (row['error'] or '').lower() or 'public' in (row['error'] or '').lower()


def test_skipped_is_terminal_and_not_retried(client, store_root, monkeypatch):
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: _FakeResponse())
    _queue('http://localhost/secret')
    images.fetch_pending()

    assert images.fetch_pending() == 0


def test_non_image_content_type_is_skipped(client, store_root, monkeypatch):
    monkeypatch.setattr(
        images.requests, 'get',
        lambda *a, **k: _FakeResponse(b'<html>', content_type='text/html'),
    )
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    digest = _queue('https://cdn.example/not-an-image')

    images.fetch_pending()

    assert _row(digest)['status'] == 'skipped'
    assert [p for p in store_root.rglob('*') if p.is_file()] == []


def test_oversized_image_stops_reading_and_is_skipped(client, store_root, monkeypatch):
    """The ceiling is enforced while reading, not from Content-Length, which
    is advisory and absent on chunked responses."""
    monkeypatch.setattr(media, 'MAX_IMAGE_BYTES', 100)
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: _FakeResponse(b'x' * 5000))
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    digest = _queue('https://cdn.example/huge.png')

    images.fetch_pending()

    assert _row(digest)['status'] == 'skipped'


def test_network_failure_is_retryable_then_gives_up(client, store_root, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError('connection reset')

    monkeypatch.setattr(images.requests, 'get', _boom)
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    digest = _queue('https://cdn.example/flaky.png')

    for _ in range(5):
        images.fetch_pending()

    row = _row(digest)
    assert row['status'] == 'failed'
    assert row['attempt_count'] == images._MAX_ATTEMPTS
    assert images.fetch_pending() == 0


def test_unavailable_store_pauses_instead_of_failing_rows(client, tmp_path, monkeypatch):
    """An unplugged drive must not burn through the queue marking everything
    failed — the rows have to still be there when it comes back."""
    monkeypatch.setenv('EMAIL_MEDIA_ROOT', str(tmp_path / 'not-mounted'))
    calls = []
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: calls.append(1) or _FakeResponse())
    digest = _queue('https://cdn.example/logo.png')

    assert images.fetch_pending() == 0
    assert calls == []
    assert _row(digest)['status'] == 'pending'


def test_queue_images_never_resets_an_existing_row(client, store_root, monkeypatch):
    monkeypatch.setattr(images.requests, 'get', lambda *a, **k: _FakeResponse(b'DATA'))
    monkeypatch.setattr(images, 'assert_public_url', lambda u: u)
    url = 'https://cdn.example/logo.png'
    digest = _queue(url)
    images.fetch_pending()
    assert _row(digest)['status'] == 'stored'

    # Seeing the same logo in a later email must not re-queue stored bytes.
    _queue(url)

    assert _row(digest)['status'] == 'stored'
    assert images.fetch_pending() == 0
