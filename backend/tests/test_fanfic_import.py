"""End-to-end import pipeline tests with the network monkeypatched to
fixture HTML. Background threads are made synchronous so assertions can run
right after the request returns."""
from pathlib import Path

import pytest

from backend.fanfic import download

FIXTURES = Path(__file__).parent / 'fixtures' / 'fanfic'

THREAD = 'https://forums.spacebattles.com/threads/a-test-fic.12345'


class FakeResp:
    def __init__(self, text, url):
        self.text = text
        self.url = url


def _fixture_map() -> dict[str, str]:
    return {
        f'{THREAD}/': (FIXTURES / 'thread_page.html').read_text(),
        f'{THREAD}/threadmarks': (FIXTURES / 'threadmarks_index.html').read_text(),
        f'{THREAD}/reader?threadmark_category=1': (FIXTURES / 'reader_p1.html').read_text(),
        f'{THREAD}/reader/page-2?threadmark_category=1': (FIXTURES / 'reader_p2.html').read_text(),
        f'{THREAD}/reader?threadmark_category=2': (FIXTURES / 'reader_side_p1.html').read_text(),
    }


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    """Sync import, zero delay, isolated file root, fixture-backed fetches."""
    from backend.routes import fanfic as fanfic_routes

    monkeypatch.setenv('FANFIC_ROOT', str(tmp_path / 'fanfic'))
    monkeypatch.setattr(download, 'REQUEST_DELAY', 0)
    monkeypatch.setattr(fanfic_routes, '_start_import_bg', download.run_import)
    monkeypatch.setattr(fanfic_routes, '_start_update_bg', download.run_check_updates)

    pages = _fixture_map()
    binaries: dict[str, tuple[bytes, str]] = {
        'https://example.com/art.png': (b'\x89PNG-fake-bytes', 'image/png'),
    }

    def fetch(url):
        if url not in pages:
            raise RuntimeError(f'404 for {url}')
        return FakeResp(pages[url], url)

    def fetch_binary(url):
        if url not in binaries:
            raise RuntimeError(f'404 for {url}')
        return binaries[url]

    monkeypatch.setattr(download, '_fetch', fetch)
    monkeypatch.setattr(download, '_fetch_binary', fetch_binary)
    return {'pages': pages, 'binaries': binaries, 'root': tmp_path / 'fanfic'}


def _import_fic(client) -> str:
    resp = client.post('/api/fanfic/import', json={'url': f'{THREAD}/page-2'})
    assert resp.status_code == 202, resp.get_json()
    return resp.get_json()['id']


def _site_tags(fic_id: str) -> list[str]:
    from backend.db.connection import get_db
    rows = get_db().execute(
        'SELECT name FROM fic_site_tags WHERE fic_id=? ORDER BY created_at, rowid',
        (fic_id,)).fetchall()
    return [r['name'] for r in rows]


def test_full_import(client, fake_net):
    fic_id = _import_fic(client)

    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['title'] == 'A Test Fic'
    assert fic['author'] == 'TestAuthor'
    assert fic['description'] == 'A story about testing things.'
    assert fic['downloadStatus'] == 'complete'
    assert fic['chapterCount'] == 4
    assert fic['wordCount'] > 0
    assert fic['site'] == 'forums.spacebattles.com'

    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    assert [c['title'] for c in chapters] == [
        'Chapter One', 'Chapter Two', 'Chapter Three', 'Omake: The Beach Episode']
    assert [c['category'] for c in chapters] == ['Threadmarks'] * 3 + ['Sidestory']
    assert chapters[0]['position'] == 1 and chapters[2]['position'] == 3
    assert all('contentHtml' not in c for c in chapters)
    assert chapters[0]['postedAt'] is not None

    # Chapter content: sanitized, image rewritten to local API path
    ch1 = client.get(f"/api/fanfic/chapters/{chapters[0]['id']}").get_json()
    assert 'Tuesday' in ch1['contentHtml']
    assert '<script' not in ch1['contentHtml']
    assert 'onclick' not in ch1['contentHtml']
    assert f'/api/fanfic/{fic_id}/images/' in ch1['contentHtml']
    assert ch1['wordCount'] > 0

    # The image landed on disk and is served
    img_dir = fake_net['root'] / fic_id / 'images'
    files = list(img_dir.glob('*.png'))
    assert len(files) == 1
    served = client.get(f'/api/fanfic/{fic_id}/images/{files[0].name}')
    assert served.status_code == 200
    assert served.data == b'\x89PNG-fake-bytes'

    # Cover picked from the first chapter's downloaded image
    assert fic['coverPath'] == files[0].name

    # Progress reports done
    assert client.get(f'/api/fanfic/{fic_id}/status').get_json()['done'] is True

    # Site tags scraped from the main thread page
    assert _site_tags(fic_id) == ['isekai', 'time travel']


def test_tag_fetch_failure_tolerated(client, fake_net):
    del fake_net['pages'][f'{THREAD}/']
    fic_id = _import_fic(client)
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['downloadStatus'] == 'complete'
    assert _site_tags(fic_id) == []


def test_check_updates_refreshes_tags(client, fake_net):
    fic_id = _import_fic(client)
    assert _site_tags(fic_id) == ['isekai', 'time travel']
    # Site tags changed; check-updates backfills/replaces them wholesale
    fake_net['pages'][f'{THREAD}/'] = (
        '<div class="tagList"><a class="tagItem">complete</a>'
        '<a class="tagItem">isekai</a></div>')
    assert client.post(f'/api/fanfic/{fic_id}/check-updates').status_code == 202
    assert _site_tags(fic_id) == ['complete', 'isekai']


def test_empty_tag_page_keeps_existing_tags(client, fake_net):
    fic_id = _import_fic(client)
    # A login wall returns HTTP 200 with no tag list — must not wipe tags
    fake_net['pages'][f'{THREAD}/'] = '<html><body><h1>Log in</h1></body></html>'
    assert client.post(f'/api/fanfic/{fic_id}/check-updates').status_code == 202
    assert _site_tags(fic_id) == ['isekai', 'time travel']


def test_reimport_returns_existing(client, fake_net):
    fic_id = _import_fic(client)
    resp = client.post('/api/fanfic/import', json={'url': f'{THREAD}/post-101'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {'id': fic_id, 'alreadyExists': True}
    assert len(client.get('/api/fanfic').get_json()) == 1


def test_blocked_fetch_surfaces_cookie_hint(client, fake_net, monkeypatch):
    def blocked(url):
        raise download.FetchBlockedError(
            "forums.spacebattles.com blocked by the site's bot protection. "
            "paste your browser session's Cookie header in Settings")
    monkeypatch.setattr(download, '_fetch', blocked)
    resp = client.post('/api/fanfic/import', json={'url': f'{THREAD}/'})
    assert resp.status_code == 202  # resolution didn't need a fetch; job fails
    fic_id = resp.get_json()['id']
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['downloadStatus'] == 'error'
    assert 'Cookie' in fic['downloadError']


def test_import_no_threadmarks_is_error(client, fake_net):
    fake_net['pages'][f'{THREAD}/threadmarks'] = '<h1 class="p-title-value">Empty</h1>'
    del fake_net['pages'][f'{THREAD}/reader?threadmark_category=1']
    # the fallback's threadmark listing is also empty
    fake_net['pages'][f'{THREAD}/threadmarks?threadmark_category=1'] = '<html><body></body></html>'
    fic_id = client.post('/api/fanfic/import', json={'url': f'{THREAD}/'}).get_json()['id']
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['downloadStatus'] == 'error'
    assert 'threadmark' in fic['downloadError'].lower()


FALLBACK_PAGES = {
    # reader-less site (QQ): threadmark listings + posts resolved via thread pages
    f'{THREAD}/threadmarks?threadmark_category=1': 'threadmarks_list_p1.html',
    f'{THREAD}/threadmarks?threadmark_category=1&page=2': 'threadmarks_list_p2.html',
    f'{THREAD}/threadmarks?threadmark_category=2': 'threadmarks_list_side.html',
    f'{THREAD}/post-101': 'reader_p1.html',
    f'{THREAD}/post-103': 'reader_p2.html',
    f'{THREAD}/post-201': 'reader_side_p1.html',
}


def _enable_fallback_pages(fake_net):
    """Remove the reader endpoints (QQ forbids them) and provide the
    threadmark listings + thread pages the fallback walks instead."""
    for key in list(fake_net['pages']):
        if '/reader' in key:
            del fake_net['pages'][key]
    for url, name in FALLBACK_PAGES.items():
        fake_net['pages'][url] = (FIXTURES / name).read_text()


def test_import_falls_back_when_reader_unavailable(client, fake_net):
    _enable_fallback_pages(fake_net)
    fic_id = _import_fic(client)

    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['downloadStatus'] == 'complete'
    assert fic['title'] == 'A Test Fic'
    assert fic['chapterCount'] == 4

    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    assert [c['title'] for c in chapters] == [
        'Chapter One', 'Chapter Two', 'Chapter Three', 'Omake: The Beach Episode']
    assert [c['position'] for c in chapters] == [1, 2, 3, 1]
    assert [c['category'] for c in chapters] == ['Threadmarks'] * 3 + ['Sidestory']

    # images still downloaded + rewritten on the fallback path
    ch1 = client.get(f"/api/fanfic/chapters/{chapters[0]['id']}").get_json()
    assert f'/api/fanfic/{fic_id}/images/' in ch1['contentHtml']
    assert '<script' not in ch1['contentHtml']


def test_reimport_restarts_failed_download(client, fake_net):
    # First import fails hard: no reader, no threadmark listings
    for key in list(fake_net['pages']):
        if '/reader' in key:
            del fake_net['pages'][key]
    fic_id = client.post('/api/fanfic/import', json={'url': f'{THREAD}/'}).get_json()['id']
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['downloadStatus'] == 'error'

    # The site recovers; pasting the same URL restarts instead of "already exists"
    _enable_fallback_pages(fake_net)
    resp = client.post('/api/fanfic/import', json={'url': f'{THREAD}/'})
    assert resp.status_code == 202
    assert resp.get_json() == {'id': fic_id, 'restarted': True}

    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['downloadStatus'] == 'complete'
    assert fic['chapterCount'] == 4
    assert fic['title'] == 'A Test Fic'  # placeholder title was refreshed

    # A healthy fic is still reported as existing, not restarted
    resp = client.post('/api/fanfic/import', json={'url': f'{THREAD}/post-101'})
    assert resp.status_code == 200
    assert resp.get_json() == {'id': fic_id, 'alreadyExists': True}


def test_check_updates_appends_new_chapter(client, fake_net):
    fic_id = _import_fic(client)
    # A new threadmark appears: index count bumps to 4 and page 2 gains a post
    fake_net['pages'][f'{THREAD}/threadmarks'] = \
        fake_net['pages'][f'{THREAD}/threadmarks'].replace(
            'Statistics (3 threadmarks', 'Statistics (4 threadmarks')
    fake_net['pages'][f'{THREAD}/reader/page-2?threadmark_category=1'] = """
    <article class="message" data-author="TestAuthor" data-content="post-103">
      <span class="threadmarkLabel">Chapter Three</span>
      <div class="bbWrapper">old</div></article>
    <article class="message" data-author="TestAuthor" data-content="post-104">
      <span class="threadmarkLabel">Chapter Four</span>
      <time class="u-dt" data-time="1600400000"></time>
      <div class="bbWrapper">Brand new chapter content here.</div></article>
    """
    resp = client.post(f'/api/fanfic/{fic_id}/check-updates')
    assert resp.status_code == 202

    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    main = [c for c in chapters if c['category'] == 'Threadmarks']
    assert [c['title'] for c in main] == [
        'Chapter One', 'Chapter Two', 'Chapter Three', 'Chapter Four']
    assert main[-1]['position'] == 4
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['chapterCount'] == 5
    assert fic['lastCheckedAt'] is not None

    # Idempotent: running again adds nothing
    client.post(f'/api/fanfic/{fic_id}/check-updates')
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['chapterCount'] == 5


PROXY_ART = ('https://forums.spacebattles.com/proxy.php'
             '?image=https%3A%2F%2Fexample.com%2Fart.png&hash=abc123')


def test_image_falls_back_to_forum_proxy(client, fake_net):
    # The original image host is dead, but the forum's proxy cache has a copy
    # (the reader_p1 fixture's img carries both URLs, like real XenForo output).
    del fake_net['binaries']['https://example.com/art.png']
    fake_net['binaries'][PROXY_ART] = (b'\x89PNG-proxy-bytes', 'image/png')

    fic_id = _import_fic(client)
    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    ch1 = client.get(f"/api/fanfic/chapters/{chapters[0]['id']}").get_json()
    assert f'/api/fanfic/{fic_id}/images/' in ch1['contentHtml']
    assert 'example.com/art.png' not in ch1['contentHtml']
    files = list((fake_net['root'] / fic_id / 'images').glob('*.png'))
    assert len(files) == 1
    assert files[0].read_bytes() == b'\x89PNG-proxy-bytes'


def test_check_updates_repairs_missing_images(client, fake_net):
    # Import while the image is unreachable: the chapter keeps the remote src.
    saved = dict(fake_net['binaries'])
    fake_net['binaries'].clear()
    fic_id = _import_fic(client)
    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    ch1 = client.get(f"/api/fanfic/chapters/{chapters[0]['id']}").get_json()
    assert 'https://example.com/art.png' in ch1['contentHtml']
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['coverPath'] is None

    # Later the forum's proxy copy is reachable; ↻ Update re-fetches the
    # chapter's thread page and repairs the image.
    fake_net['binaries'][PROXY_ART] = saved['https://example.com/art.png']
    fake_net['pages'][f'{THREAD}/post-101'] = (FIXTURES / 'reader_p1.html').read_text()
    assert client.post(f'/api/fanfic/{fic_id}/check-updates').status_code == 202

    ch1 = client.get(f"/api/fanfic/chapters/{chapters[0]['id']}").get_json()
    assert f'/api/fanfic/{fic_id}/images/' in ch1['contentHtml']
    assert 'example.com/art.png' not in ch1['contentHtml']
    # the backfilled image also becomes the cover
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['coverPath'] is not None


def test_check_updates_repair_tolerates_fetch_failure(client, fake_net):
    # Broken images whose pages can't be re-fetched are left alone and the
    # update still completes.
    fake_net['binaries'].clear()
    fic_id = _import_fic(client)
    # no f'{THREAD}/post-101' page registered -> repair fetch fails
    assert client.post(f'/api/fanfic/{fic_id}/check-updates').status_code == 202
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['downloadStatus'] == 'complete'


def test_check_updates_rejected_for_file_fics(client, fake_net):
    from backend.db.connection import get_db
    get_db().execute(
        "INSERT INTO fics(id, title, source_type, created_at, updated_at)"
        " VALUES ('pdf1', 'Doc', 'pdf', 0, 0)")
    get_db().commit()
    assert client.post('/api/fanfic/pdf1/check-updates').status_code == 400


def test_cookie_jar_built_per_site(client, fake_net):
    """Stored cookies are parsed into a jar dict for the URL's site (with or
    without a www. prefix) — a jar survives redirects where a raw Cookie
    header would be stripped by requests."""
    assert client.put('/api/fanfic/cookies', json={
        'domain': 'forums.spacebattles.com',
        'cookie': 'xf_session=abc123; xf_user=87465,tok',
    }).status_code == 200

    jar = download._cookies_for('https://www.forums.spacebattles.com/threads/x.1/')
    assert jar == {'xf_session': 'abc123', 'xf_user': '87465,tok'}
    assert download._cookies_for('https://forums.sufficientvelocity.com/threads/y.2/') is None
    assert 'User-Agent' in download._headers('https://forums.spacebattles.com/')


def test_cookies_never_echoed(client, fake_net):
    client.put('/api/fanfic/cookies', json={
        'domain': 'forum.questionablequesting.com', 'cookie': 'xf_user=secret'})
    listing = client.get('/api/fanfic/cookies').get_json()
    qq = next(c for c in listing if c['domain'] == 'forum.questionablequesting.com')
    assert qq['hasCookie'] is True
    assert 'secret' not in str(listing)
    sb = next(c for c in listing if c['domain'] == 'forums.spacebattles.com')
    assert sb['hasCookie'] is False
    # Empty cookie deletes
    client.put('/api/fanfic/cookies', json={
        'domain': 'forum.questionablequesting.com', 'cookie': ''})
    listing = client.get('/api/fanfic/cookies').get_json()
    qq = next(c for c in listing if c['domain'] == 'forum.questionablequesting.com')
    assert qq['hasCookie'] is False


def test_cookie_unknown_domain_rejected(client, fake_net):
    resp = client.put('/api/fanfic/cookies', json={'domain': 'evil.com', 'cookie': 'x'})
    assert resp.status_code == 400


def test_fetch_retries_transient_403(client, monkeypatch):
    """A burst-rate-limit 403 is retried with backoff; a Cloudflare
    challenge is not."""
    import sys
    from types import SimpleNamespace

    calls = {'n': 0}
    sleeps: list[float] = []

    def fake_get(url, timeout=None, headers=None, cookies=None):
        calls['n'] += 1
        status = 403 if calls['n'] < 3 else 200
        return SimpleNamespace(
            status_code=status, headers={}, text='ok', url=url,
            raise_for_status=lambda: None)

    monkeypatch.setitem(sys.modules, 'requests', SimpleNamespace(get=fake_get))
    monkeypatch.setattr(download.time, 'sleep', lambda s: sleeps.append(s))

    resp = download._fetch('https://forum.questionablequesting.com/threads/x.1/')
    assert resp.status_code == 200
    assert calls['n'] == 3
    assert sleeps[:2] == [5, 15]

    # Cloudflare challenge: immediate FetchBlockedError, no retries
    calls['n'] = 0

    def cf_get(url, timeout=None, headers=None, cookies=None):
        calls['n'] += 1
        return SimpleNamespace(status_code=403, headers={'CF-RAY': 'x'},
                               text='Just a moment', url=url)

    monkeypatch.setitem(sys.modules, 'requests', SimpleNamespace(get=cf_get))
    with pytest.raises(download.FetchBlockedError):
        download._fetch('https://forums.spacebattles.com/threads/x.1/')
    assert calls['n'] == 1


def test_cookie_input_normalization(client, fake_net):
    """Pasting a Firefox 'Copy Request Headers' dump or a cURL command stores
    just the Cookie value."""
    from backend.routes.fanfic import _normalize_cookie_input

    header_dump = (
        'GET /threads/x.1/ HTTP/2\n'
        'Host: forums.spacebattles.com\n'
        'User-Agent: Mozilla/5.0\n'
        'Cookie: xf_user=u123; xf_session=s456; cf_clearance=cf789\n'
        'Accept-Language: en-US\n'
    )
    assert _normalize_cookie_input(header_dump) == 'xf_user=u123; xf_session=s456; cf_clearance=cf789'

    curl_cmd = ("curl 'https://forums.spacebattles.com/threads/x.1/' "
                "-H 'User-Agent: Mozilla/5.0' -H 'Cookie: xf_user=u123; xf_session=s456'")
    assert _normalize_cookie_input(curl_cmd) == 'xf_user=u123; xf_session=s456'

    assert _normalize_cookie_input("curl -b 'xf_user=u123' https://x.com") == 'xf_user=u123'
    assert _normalize_cookie_input('xf_user=u123; xf_session=s456') == 'xf_user=u123; xf_session=s456'
    assert _normalize_cookie_input('Cookie: xf_user=u123') == 'xf_user=u123'

    # Firefox Network panel > Cookies tab > "Copy All" JSON
    ff_json = '''{
        "Request Cookies": {
            "xf_csrf": "abc",
            "xf_session": "s456",
            "xf_user": "87465,token"
        }
    }'''
    assert _normalize_cookie_input(ff_json) == 'xf_csrf=abc; xf_session=s456; xf_user=87465,token'
    # bare name->value JSON works too
    assert _normalize_cookie_input('{"xf_user": "u123", "xf_session": "s456"}') == \
        'xf_user=u123; xf_session=s456'

    # End-to-end: paste the dump, the parsed jar carries every cookie
    client.put('/api/fanfic/cookies', json={
        'domain': 'forums.spacebattles.com', 'cookie': header_dump})
    from backend.fanfic import download
    jar = download._cookies_for('https://forums.spacebattles.com/threads/x.1/')
    assert jar == {'xf_user': 'u123', 'xf_session': 's456', 'cf_clearance': 'cf789'}


def test_stale_downloading_status_reset_on_startup(client):
    """A fic left 'downloading' by a killed/replaced process (e.g. the dev
    server's autoreloader restarting mid-download) has no thread left to
    finish it — the in-memory progress registry starts empty on every
    process start, so the next startup must flip any such row to 'error'
    instead of leaving the UI spinning forever."""
    from backend.db.connection import get_db, init_db

    db = get_db()
    db.execute(
        "INSERT INTO fics(id, title, source_type, site, thread_id, download_status,"
        " created_at, updated_at) VALUES ('stale1','x','xenforo','forums.spacebattles.com',"
        " '999','downloading',0,0)")
    db.commit()

    init_db()

    row = db.execute(
        "SELECT download_status, download_error FROM fics WHERE id='stale1'").fetchone()
    assert row['download_status'] == 'error'
    assert 'restart' in row['download_error']


def test_import_rejects_bad_urls(client, fake_net):
    assert client.post('/api/fanfic/import', json={}).status_code == 400
    assert client.post('/api/fanfic/import', json={'url': 'file:///etc/passwd'}).status_code == 400
    resp = client.post('/api/fanfic/import', json={'url': 'https://archiveofourown.org/works/1'})
    assert resp.status_code == 422


def test_delete_cancels_and_removes(client, fake_net):
    fic_id = _import_fic(client)
    img_dir = fake_net['root'] / fic_id
    assert img_dir.is_dir()
    assert client.delete(f'/api/fanfic/{fic_id}').status_code == 200
    assert client.get(f'/api/fanfic/{fic_id}').status_code == 404
    assert client.get(f'/api/fanfic/{fic_id}/chapters').get_json() == []
    assert not img_dir.exists()
    assert download.get_progress(fic_id) is None


def test_cancellation_mid_import(client, fake_net, monkeypatch):
    """Removing the progress entry (as DELETE does) aborts the walker."""
    from backend.db.connection import get_db
    from backend.fanfic.xenforo import ThreadRef

    real_fetch = download._fetch

    def fetch_and_cancel(url):
        resp = real_fetch(url)
        if 'reader' in url:
            download.cancel_progress('cancelme')
        return resp

    monkeypatch.setattr(download, '_fetch', fetch_and_cancel)
    get_db().execute(
        "INSERT INTO fics(id, title, source_type, site, thread_id, download_status,"
        " created_at, updated_at) VALUES ('cancelme','x','xenforo','forums.spacebattles.com',"
        " '12345','downloading',0,0)")
    get_db().commit()
    download.start_progress('cancelme', 'index')
    download.run_import('cancelme', ThreadRef('forums.spacebattles.com', '12345', 'a-test-fic'))
    # Only the first reader page (2 chapters) was ingested before the abort
    rows = get_db().execute(
        "SELECT COUNT(*) AS n FROM fic_chapters WHERE fic_id='cancelme'").fetchone()
    assert rows['n'] == 2
