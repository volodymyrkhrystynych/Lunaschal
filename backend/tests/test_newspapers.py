"""Route + scraper tests for the newspaper front-page archive. No real
network calls: scraper.fetch_image_url/download_image are monkeypatched."""
from datetime import date

import pytest

from backend.newspapers import scraper, storage


@pytest.fixture(autouse=True)
def newspapers_root(monkeypatch, tmp_path):
    monkeypatch.setenv('NEWSPAPERS_ROOT', str(tmp_path / 'newspapers'))
    return tmp_path / 'newspapers'


@pytest.fixture
def mock_fetch(monkeypatch):
    calls = {'fetch': 0, 'download': 0}

    def fake_fetch_image_url(page_url):
        calls['fetch'] += 1
        return f'https://www.frontpages.com/g/fake/{page_url.split("/")[-2]}.webp'

    def fake_download_image(image_url):
        calls['download'] += 1
        return b'fake-image-bytes', 'image/webp'

    monkeypatch.setattr('backend.newspapers.sync.scraper.fetch_image_url', fake_fetch_image_url)
    monkeypatch.setattr('backend.newspapers.sync.scraper.download_image', fake_download_image)
    return calls


def test_sync_downloads_both_papers(client, mock_fetch, newspapers_root):
    resp = client.post('/api/newspapers/sync')
    assert resp.status_code == 200
    results = resp.get_json()
    assert {r['paper'] for r in results} == {'toronto-star', 'nyt'}
    assert all(r['status'] == 'downloaded' for r in results)
    assert mock_fetch['fetch'] == 2
    assert mock_fetch['download'] == 2

    today = date.today().isoformat()
    for paper in ('toronto-star', 'nyt'):
        saved = newspapers_root / paper / f'{today}.webp'
        assert saved.is_file()
        assert saved.read_bytes() == b'fake-image-bytes'


def test_sync_is_idempotent(client, mock_fetch):
    client.post('/api/newspapers/sync')
    resp = client.post('/api/newspapers/sync')
    results = resp.get_json()
    assert all(r['status'] == 'already-saved' for r in results)
    # Second sync shouldn't have hit the network again.
    assert mock_fetch['fetch'] == 2
    assert mock_fetch['download'] == 2


def test_frontpages_by_date(client, mock_fetch):
    today = date.today().isoformat()
    client.post('/api/newspapers/sync')

    resp = client.get(f'/api/newspapers/frontpages/{today}')
    entries = {e['paper']: e for e in resp.get_json()}
    assert entries['toronto-star']['imageUrl'] == f'/api/newspapers/image/toronto-star/{today}'
    assert entries['nyt']['imageUrl'] == f'/api/newspapers/image/nyt/{today}'

    resp = client.get('/api/newspapers/frontpages/2000-01-01')
    entries = {e['paper']: e for e in resp.get_json()}
    assert entries['toronto-star']['imageUrl'] is None
    assert entries['nyt']['imageUrl'] is None


def test_serve_image(client, mock_fetch):
    today = date.today().isoformat()
    client.post('/api/newspapers/sync')

    resp = client.get(f'/api/newspapers/image/toronto-star/{today}')
    assert resp.status_code == 200
    assert resp.data == b'fake-image-bytes'
    assert resp.content_type == 'image/webp'


def test_serve_image_404s(client, mock_fetch):
    today = date.today().isoformat()
    client.post('/api/newspapers/sync')

    assert client.get(f'/api/newspapers/image/not-a-paper/{today}').status_code == 404
    assert client.get('/api/newspapers/image/toronto-star/not-a-date').status_code == 404
    assert client.get('/api/newspapers/image/toronto-star/2000-01-01').status_code == 404


def test_resolve_stored_path_rejects_paths_outside_root(newspapers_root):
    assert storage.resolve_stored_path(str(newspapers_root / 'toronto-star' / '2026-07-10.webp')) == \
        newspapers_root / 'toronto-star' / '2026-07-10.webp'
    assert storage.resolve_stored_path('/etc/passwd') is None
    assert storage.resolve_stored_path(str(newspapers_root / 'toronto-star' / 'sub' / '2026-07-10.webp')) is None
    assert storage.resolve_stored_path(str(newspapers_root.parent / 'secret.txt')) is None


def test_build_path_picks_extension_from_content_type(newspapers_root):
    assert storage.build_path('toronto-star', '2026-07-10', 'image/webp') == \
        newspapers_root / 'toronto-star' / '2026-07-10.webp'
    assert storage.build_path('nyt', '2026-07-10', 'image/jpeg') == \
        newspapers_root / 'nyt' / '2026-07-10.jpg'
    # Unknown/missing content-type falls back to a sane default.
    assert storage.build_path('nyt', '2026-07-10', '') == newspapers_root / 'nyt' / '2026-07-10.jpg'


def test_sync_reports_error_without_blocking_other_paper(client, monkeypatch, newspapers_root):
    def failing_fetch(page_url):
        if 'toronto-star' in page_url:
            raise ValueError('boom')
        return 'https://www.frontpages.com/g/fake/nyt.webp'

    monkeypatch.setattr('backend.newspapers.sync.scraper.fetch_image_url', failing_fetch)
    monkeypatch.setattr('backend.newspapers.sync.scraper.download_image', lambda url: (b'ok', 'image/webp'))

    resp = client.post('/api/newspapers/sync')
    results = {r['paper']: r for r in resp.get_json()}
    assert results['toronto-star']['status'] == 'error'
    assert 'boom' in results['toronto-star']['error']
    assert results['nyt']['status'] == 'downloaded'


def test_fetch_image_url_decodes_obfuscated_script(monkeypatch):
    # og:image is a decoy that 404s on the live site; the real path is
    # base64-encoded inside a tiny inline script (see scraper.py docstring).
    # This is the real payload captured from the live site during development.
    html = (
        "<html><body><script data-cfasync=\"false\">(function(){var gim="
        "document.getElementById('gi'+'ornale-i'+'mg');"
        "var u=atob('L2cvMjAyNi8wNy8wOS90b3JvbnRvLXN0YXItMDc0MzIxZzN5Mm9hNi53ZWJw');"
        "gim.src=u;})();</script></body></html>"
    )

    class FakeResponse:
        text = html

        def raise_for_status(self):
            pass

    monkeypatch.setattr('requests.get', lambda *a, **kw: FakeResponse())

    url = scraper.fetch_image_url('https://www.frontpages.com/toronto-star/')
    assert url == 'https://www.frontpages.com/g/2026/07/09/toronto-star-074321g3y2oa6.webp'


def test_fetch_image_url_raises_without_script(monkeypatch):
    class FakeResponse:
        text = '<html><head></head></html>'

        def raise_for_status(self):
            pass

    monkeypatch.setattr('requests.get', lambda *a, **kw: FakeResponse())

    with pytest.raises(ValueError):
        scraper.fetch_image_url('https://www.frontpages.com/toronto-star/')
