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
    monkeypatch.setattr(fanfic_routes, '_start_drain_bg', download.run_drain_pending)

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
    assert fic['description'] == (
        'A complete threadmark synopsis with more detail than the social preview. '
        'It can contain multiple paragraphs.'
    )
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


# --- edited chapters ---
#
# XenForo raises no alert when an author edits an existing post and leaves the
# threadmarks index untouched, so nothing about the fic looks different from
# outside. Only re-reading the post reveals the change, which is what the deep
# check does and the cheap one deliberately does not.

def _rewrite_chapter_two(fake_net, *, edited_at: int | None, body: str) -> None:
    """Replace reader page 1 so post-102 carries new prose, optionally with a
    "Last edited" notice. Post 101 is left byte-identical."""
    edit_block = (
        f'<div class="message-lastEdit">Last edited:'
        f' <time class="u-dt" data-time="{edited_at}"></time></div>'
        if edited_at is not None else ''
    )
    fake_net['pages'][f'{THREAD}/reader?threadmark_category=1'] = f"""
    <article class="message" data-author="TestAuthor" data-content="post-101">
      <span class="threadmarkLabel">Chapter One</span>
      <div class="message-attribution">
        <time class="u-dt" data-time="1600000000"></time></div>
      <div class="bbWrapper">It began on a <b>Tuesday</b>.</div></article>
    <article class="message" data-author="TestAuthor" data-content="post-102">
      <span class="threadmarkLabel">Chapter Two</span>
      <div class="message-attribution">
        <time class="u-dt" data-time="1600100000"></time></div>
      <div class="bbWrapper">{body}</div>
      {edit_block}</article>
    <nav class="pageNavWrapper"><ul class="pageNav-main">
      <li class="pageNav-page"><a href="#">1</a></li>
      <li class="pageNav-page"><a href="#">2</a></li>
    </ul></nav>
    """


def _chapter_named(client, fic_id: str, title: str) -> dict:
    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    summary = next(c for c in chapters if c['title'] == title)
    return client.get(f"/api/fanfic/chapters/{summary['id']}").get_json()


def test_deep_check_rewrites_an_edited_chapter(client, fake_net):
    fic_id = _import_fic(client)
    before = _chapter_named(client, fic_id, 'Chapter Two')
    assert 'rewrote this scene' not in before['contentHtml']

    _rewrite_chapter_two(
        fake_net, edited_at=1700000000,
        body='The author rewrote this scene entirely, and it is now'
             ' considerably longer than the version we downloaded.')
    resp = client.post(f'/api/fanfic/{fic_id}/check-updates', json={'deep': True})
    assert resp.status_code == 202
    assert resp.get_json()['deep'] is True

    after = _chapter_named(client, fic_id, 'Chapter Two')
    assert 'rewrote this scene' in after['contentHtml']
    assert 'rewrote this scene' in after['contentText']
    assert after['editedAt'] is not None
    assert after['wordCount'] > before['wordCount']
    # the chapter is revised in place: same row, same reading position, and no
    # phantom chapter appended
    assert after['id'] == before['id']
    assert after['position'] == before['position']
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['chapterCount'] == 4


def test_routine_check_never_escalates_itself_to_a_deep_one(client, fake_net):
    """The tier boundary, stated out loud. Finding an edit costs a full re-walk
    and authors revising published chapters is rare, so nothing escalates on a
    timer — a deep pass happens only when it's asked for."""
    fic_id = _import_fic(client)
    _rewrite_chapter_two(
        fake_net, edited_at=1700000000, body='The author rewrote this scene.')

    client.post(f'/api/fanfic/{fic_id}/check-updates')
    assert 'rewrote this scene' not in _chapter_named(
        client, fic_id, 'Chapter Two')['contentHtml']

    # ...and repeating it doesn't eventually trip into one either
    client.post(f'/api/fanfic/{fic_id}/check-updates')
    assert 'rewrote this scene' not in _chapter_named(
        client, fic_id, 'Chapter Two')['contentHtml']


def test_deep_check_leaves_unedited_chapters_untouched(client, fake_net):
    """A deep scan re-reads everything but must rewrite nothing when nothing
    changed — otherwise every scan churns content_text and the FTS index."""
    from backend.db.connection import get_db
    fic_id = _import_fic(client)
    db = get_db()
    db.execute("UPDATE fic_chapters SET content_html='SENTINEL' WHERE fic_id=?", (fic_id,))
    db.commit()

    client.post(f'/api/fanfic/{fic_id}/check-updates', json={'deep': True})

    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM fic_chapters WHERE fic_id=? AND content_html='SENTINEL'",
        (fic_id,)).fetchone()['n']
    assert remaining == 4


def _long_fic_pages(post_ids: list[str]) -> dict[str, str]:
    """A single-category thread of `post_ids` chapters, paginated 10 per reader
    page exactly as XenForo does, plus the matching threadmarks index."""
    pages: dict[str, str] = {}
    page_count = -(-len(post_ids) // 10)

    def nav() -> str:
        links = ''.join(
            f'<li class="pageNav-page"><a href="#">{i}</a></li>'
            for i in range(1, page_count + 1))
        return f'<nav class="pageNavWrapper"><ul class="pageNav-main">{links}</ul></nav>'

    for page in range(1, page_count + 1):
        chunk = post_ids[(page - 1) * 10:page * 10]
        articles = ''.join(f"""
        <article class="message" data-author="TestAuthor" data-content="post-{pid}">
          <span class="threadmarkLabel">Chapter {pid}</span>
          <div class="message-attribution">
            <time class="u-dt" data-time="{1600000000 + int(pid)}"></time></div>
          <div class="bbWrapper">Body of chapter {pid}.</div></article>
        """ for pid in chunk)
        suffix = '' if page == 1 else f'/page-{page}'
        pages[f'{THREAD}/reader{suffix}?threadmark_category=1'] = articles + nav()

    rows = ''.join(f"""
    <div class="structItem structItem--threadmark">
      <div class="structItem-title"><a href="/threads/a-test-fic.12345/post-{pid}">
        Chapter {pid}</a></div>
      <time data-time="{1600000000 + int(pid)}"></time>
    </div>
    """ for pid in post_ids)
    pages[f'{THREAD}/threadmarks?threadmark_category=1'] = f'<html><body>{rows}</body></html>'

    pages[f'{THREAD}/'] = (FIXTURES / 'thread_page.html').read_text()
    pages[f'{THREAD}/threadmarks'] = f"""
    <html><body>
      <h1 class="p-title-value">Threadmarks for: A Test Fic</h1>
      <div class="block-tabHeader--threadmarkCategoryTabs">
        <a class="tabs-tab" href="/threads/a-test-fic.12345/threadmarks">Threadmarks</a>
      </div>
      <li aria-labelledby="threadmark-category-1">
        Statistics ({len(post_ids)} threadmarks, 1.2k words)
      </li>
    </body></html>
    """
    return pages


def test_check_updates_survives_the_site_losing_a_threadmark(client, fake_net):
    """A fic could latch into "permanently up to date".

    The category was skipped when the site's threadmark count was `<=` our row
    count, so once the site's count fell *below* ours — an author un-threadmarks
    or deletes a post, or a category is renamed under our rows — the comparison
    stayed true forever and every subsequent chapter was ignored. The count is
    now only trusted when it matches exactly; anything else falls through to
    the post-id diff."""
    from backend.db.connection import get_db

    ids = [str(100 + i) for i in range(1, 13)]  # 101..112
    fake_net['pages'].clear()
    fake_net['pages'].update(_long_fic_pages(ids))
    fic_id = _import_fic(client)
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['chapterCount'] == 12

    # the author un-threadmarks two chapters, then posts a new one
    remaining = [i for i in ids if i not in ('110', '111')] + ['113']
    assert len(remaining) == 11  # the site now reports fewer than we hold
    fake_net['pages'].update(_long_fic_pages(remaining))

    client.post(f'/api/fanfic/{fic_id}/check-updates')

    titles = {c['title'] for c in
              client.get(f'/api/fanfic/{fic_id}/chapters').get_json()}
    assert 'Chapter 113' in titles
    # the un-threadmarked chapters are kept: we downloaded them, and the site
    # dropping a threadmark is not a reason to destroy the reader's copy
    assert {'Chapter 110', 'Chapter 111'} <= titles
    assert get_db().execute(
        'SELECT COUNT(*) AS n FROM fic_chapters WHERE fic_id=?',
        (fic_id,)).fetchone()['n'] == 13


def test_check_updates_sees_swapped_threadmarks_at_an_unchanged_count(client, fake_net):
    """Equal counts do not mean equal contents.

    A category that loses two threadmarks and gains two reports the same
    "Statistics (N threadmarks)" it did before. Any count-based shortcut agrees
    the fic is current and skips it; only diffing post ids notices."""
    ids = [str(100 + i) for i in range(1, 13)]  # 101..112
    fake_net['pages'].clear()
    fake_net['pages'].update(_long_fic_pages(ids))
    fic_id = _import_fic(client)

    remaining = [i for i in ids if i not in ('110', '111')] + ['113', '114']
    assert len(remaining) == len(ids)  # the site's count is unchanged
    fake_net['pages'].update(_long_fic_pages(remaining))

    client.post(f'/api/fanfic/{fic_id}/check-updates')

    titles = {c['title'] for c in
              client.get(f'/api/fanfic/{fic_id}/chapters').get_json()}
    assert {'Chapter 113', 'Chapter 114'} <= titles


def test_check_updates_recovers_a_chapter_missing_from_the_middle(client, fake_net):
    """A gap in what we stored used to be permanent.

    The reader page to resume from was derived from our row *count*, so a
    chapter missing from the middle (a failed import, a post that 403'd) shifted
    every later chapter's arithmetic and pushed the walk straight past the page
    holding the gap. Post ids are now diffed against the threadmarks index,
    which points at the page the missing chapter is actually on."""
    from backend.db.connection import get_db

    ids = [str(100 + i) for i in range(1, 13)]  # 101..112, two reader pages
    fake_net['pages'].clear()
    fake_net['pages'].update(_long_fic_pages(ids))
    fic_id = _import_fic(client)
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['chapterCount'] == 12

    db = get_db()
    db.execute('DELETE FROM fic_chapters WHERE fic_id=? AND source_post_id=?',
               (fic_id, '103'))
    db.commit()

    # The site gains chapter 113, so the category isn't skipped as unchanged.
    # 11 rows stored puts the old count-derived resume at reader page 2 —
    # past page 1, where the missing chapter 103 lives.
    fake_net['pages'].update(_long_fic_pages([*ids, '113']))

    client.post(f'/api/fanfic/{fic_id}/check-updates')

    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    titles = {c['title'] for c in chapters}
    assert 'Chapter 103' in titles  # the gap was refilled
    assert 'Chapter 113' in titles  # and the genuinely new chapter arrived


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
    from types import SimpleNamespace

    calls = {'n': 0}
    sleeps: list[float] = []

    def fake_get(url, headers=None, cookies=None, timeout=None, stream=False):
        calls['n'] += 1
        status = 403 if calls['n'] < 3 else 200
        return SimpleNamespace(
            status_code=status, headers={}, text='ok', url=url,
            raise_for_status=lambda: None)

    monkeypatch.setattr(download, '_http_get', fake_get)
    monkeypatch.setattr(download, 'time', SimpleNamespace(sleep=lambda s: sleeps.append(s)))

    resp = download._fetch('https://forum.questionablequesting.com/threads/x.1/')
    assert resp.status_code == 200
    assert calls['n'] == 3
    assert sleeps[:2] == [5, 15]

    # Cloudflare challenge: immediate FetchBlockedError, no retries
    calls['n'] = 0

    def cf_get(url, headers=None, cookies=None, timeout=None, stream=False):
        calls['n'] += 1
        return SimpleNamespace(status_code=403, headers={'CF-RAY': 'x'},
                               text='Just a moment', url=url)

    monkeypatch.setattr(download, '_http_get', cf_get)
    with pytest.raises(download.FetchBlockedError):
        download._fetch('https://forums.spacebattles.com/threads/x.1/')
    assert calls['n'] == 1


def test_cf_proxied_403_without_challenge_page_is_not_bot_blocked(client, monkeypatch):
    """cf-ray (and friends) are stamped onto every response Cloudflare
    proxies — a pass, a real bot challenge, and an ordinary app-level 403
    alike — so a bare cf- header used to be enough to call it a bot block
    even when the page wasn't a challenge at all (e.g. SpaceBattles'
    content-rating gate on Mature/NC-17 boards, a genuine permission
    error). That produced the same misleading 'blocked by bot protection'
    hint for a completely different, non-cookie problem. Without challenge
    markup in the body, it must fall through to raise_for_status and
    surface the real HTTP error instead."""
    from types import SimpleNamespace

    def raise_for_status():
        raise ValueError('403 Client Error: Forbidden for url: ...')

    def perm_get(url, headers=None, cookies=None, timeout=None, stream=False):
        return SimpleNamespace(
            status_code=403, headers={'CF-RAY': 'x'},
            text='<h1>You do not have permission to view this thread.</h1>',
            url=url, raise_for_status=raise_for_status)

    monkeypatch.setattr(download, '_http_get', perm_get)
    monkeypatch.setattr(download, 'time', SimpleNamespace(sleep=lambda s: None))

    with pytest.raises(ValueError, match='403 Client Error'):
        download._fetch('https://forums.spacebattles.com/threads/x.1/')


def test_fetch_sends_a_same_origin_referer(client, monkeypatch):
    """SpaceBattles' Cloudflare rule challenges /threads/* whenever the
    request carries no Referer or one from another origin, while leaving
    /account/alerts and /watched/threads alone — which is why alert
    refreshes kept working and every fic fetch came back 'Just a moment'.
    The value isn't inspected past its origin, so the site root is enough,
    but it must match the host being fetched: a Referer pointing at another
    forum is treated exactly like none at all."""
    from types import SimpleNamespace

    seen = []

    def fake_get(url, headers=None, cookies=None, timeout=None, stream=False):
        seen.append(headers)
        return SimpleNamespace(status_code=200, headers={}, text='ok', url=url,
                               raise_for_status=lambda: None)

    monkeypatch.setattr(download, '_http_get', fake_get)
    monkeypatch.setattr(download, 'time', SimpleNamespace(sleep=lambda s: None))

    download._fetch('https://forums.spacebattles.com/threads/x.1/reader/page-2')
    download._fetch('https://forum.questionablequesting.com/threads/y.2/')

    assert seen[0]['Referer'] == 'https://forums.spacebattles.com/'
    assert seen[1]['Referer'] == 'https://forum.questionablequesting.com/'
    # No cookie stored for either domain here, so both fall back to the
    # module default rather than a captured browser UA.
    assert seen[0]['User-Agent'] == download.USER_AGENT


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


def test_cookie_save_captures_and_uses_user_agent(client, fake_net):
    """Cloudflare only honors cf_clearance when replayed with the same
    User-Agent that solved the challenge. A full header-dump paste carries
    that UA — it must be captured and used for that domain's requests
    instead of the module's fixed default, and preserved across a later
    re-save that pastes only a bare cookie (no headers to extract a UA
    from)."""
    header_dump = (
        'GET /threads/x.1/ HTTP/2\n'
        'Host: forums.spacebattles.com\n'
        'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0\n'
        'Cookie: xf_user=u123; cf_clearance=cf789\n'
    )
    client.put('/api/fanfic/cookies', json={
        'domain': 'forums.spacebattles.com', 'cookie': header_dump})
    listing = client.get('/api/fanfic/cookies').get_json()
    sb = next(c for c in listing if c['domain'] == 'forums.spacebattles.com')
    assert sb['hasUserAgent'] is True

    headers = download._headers('https://forums.spacebattles.com/threads/x.1/')
    assert headers['User-Agent'] == 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'
    # A site with no stored cookie at all still gets the generic fallback
    assert download._headers('https://forums.sufficientvelocity.com/x')['User-Agent'] == \
        download.USER_AGENT

    # Re-saving with just a bare cookie (e.g. only the Cookie value was
    # re-copied) must not blow away the UA captured earlier.
    client.put('/api/fanfic/cookies', json={
        'domain': 'forums.spacebattles.com', 'cookie': 'xf_user=u123; cf_clearance=cf999'})
    headers = download._headers('https://forums.spacebattles.com/threads/x.1/')
    assert headers['User-Agent'] == 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'


def test_cookie_input_rejects_truncation_ellipsis(client, fake_net):
    """A devtools copy that truncates a long value (cf_clearance routinely
    exceeds what a panel renders) leaves a literal '…' in the pasted text.
    Splicing the two surviving fragments together used to be silently
    accepted, producing a token that never existed and that only failed
    later, deep inside requests, as a raw UnicodeEncodeError. Reject it at
    save time instead, with a message that says what to do."""
    from backend.routes.fanfic import _normalize_cookie_input, CookieInputError

    with pytest.raises(CookieInputError, match='…'):
        _normalize_cookie_input('xf_user=u123; cf_clearance=abc…def')

    resp = client.put('/api/fanfic/cookies', json={
        'domain': 'forums.spacebattles.com',
        'cookie': 'xf_user=u123; cf_clearance=abc…def',
    })
    assert resp.status_code == 400
    assert '…' in resp.get_json()['error']
    # Rejected input is never stored
    listing = client.get('/api/fanfic/cookies').get_json()
    sb = next(c for c in listing if c['domain'] == 'forums.spacebattles.com')
    assert sb['hasCookie'] is False


def test_legacy_non_ascii_cookie_fails_clearly_at_fetch(client, fake_net):
    """A cookie saved before the save-time check existed can still be
    sitting in the DB with a raw non-ASCII character in it. Using it must
    surface an actionable FetchBlockedError, not requests' raw
    UnicodeEncodeError from trying to latin-1-encode the Cookie header."""
    from backend.db.connection import get_db
    get_db().execute(
        "INSERT INTO site_cookies(domain, cookie, updated_at) VALUES "
        "('forums.spacebattles.com', 'xf_user=u123; cf_clearance=abc…def', 0)")
    get_db().commit()

    with pytest.raises(download.FetchBlockedError, match='non-ASCII'):
        download._cookies_for('https://forums.spacebattles.com/threads/x.1/')


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
