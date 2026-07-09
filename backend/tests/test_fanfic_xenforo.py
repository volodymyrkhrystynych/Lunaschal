"""Pure parser tests for backend/fanfic/xenforo.py against fixture HTML."""
from pathlib import Path

import pytest

from backend.fanfic import xenforo
from backend.fanfic.xenforo import (
    ThreadRef,
    UnsupportedUrlError,
    parse_reader_page,
    parse_thread_ref,
    parse_threadmarks_index,
    resolve_thread_ref,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'fanfic'


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- parse_thread_ref ---

@pytest.mark.parametrize('url', [
    'https://forums.spacebattles.com/threads/a-test-fic.12345/',
    'https://forums.spacebattles.com/threads/a-test-fic.12345',
    'https://forums.spacebattles.com/threads/a-test-fic.12345/page-31',
    'https://forums.spacebattles.com/threads/a-test-fic.12345/post-9876543',
    'https://forums.spacebattles.com/threads/a-test-fic.12345/reader/page-2',
    'https://forums.spacebattles.com/threads/a-test-fic.12345/threadmarks?threadmark_category=2',
    'https://www.forums.spacebattles.com/threads/a-test-fic.12345/',
    'https://forums.spacebattles.com/threads/a-test-fic.12345/page-3#post-111',
])
def test_parse_thread_ref_spacebattles_forms(url):
    ref = parse_thread_ref(url)
    assert ref == ThreadRef('forums.spacebattles.com', '12345', 'a-test-fic')


@pytest.mark.parametrize('domain', [
    'forums.sufficientvelocity.com',
    'forum.questionablequesting.com',
])
def test_parse_thread_ref_other_sites(domain):
    ref = parse_thread_ref(f'https://{domain}/threads/some-fic.777/')
    assert ref.domain == domain
    assert ref.thread_id == '777'


def test_parse_thread_ref_no_slug():
    ref = parse_thread_ref('https://forums.spacebattles.com/threads/12345/')
    assert ref == ThreadRef('forums.spacebattles.com', '12345', '')


@pytest.mark.parametrize('url', [
    'https://forums.spacebattles.com/posts/9876543/',
    'https://forums.spacebattles.com/forums/creative-writing.18/',
    'https://example.com/threads/a-fic.123/',
    'https://forums.spacebattles.com/',
])
def test_parse_thread_ref_rejects(url):
    assert parse_thread_ref(url) is None


def test_resolve_thread_ref_direct_no_fetch():
    def fetch(url):
        raise AssertionError('should not fetch')
    ref = resolve_thread_ref('https://forums.spacebattles.com/threads/a-test-fic.12345/', fetch)
    assert ref.thread_id == '12345'


def test_resolve_thread_ref_post_url_via_redirect():
    class Resp:
        url = 'https://forums.spacebattles.com/threads/a-test-fic.12345/post-9876543'
    calls = []
    ref = resolve_thread_ref(
        'https://forums.spacebattles.com/posts/9876543/',
        lambda url: calls.append(url) or Resp())
    assert ref == ThreadRef('forums.spacebattles.com', '12345', 'a-test-fic')
    assert calls == ['https://forums.spacebattles.com/posts/9876543/']


def test_resolve_thread_ref_unknown_host():
    with pytest.raises(UnsupportedUrlError):
        resolve_thread_ref('https://archiveofourown.org/works/12345', lambda u: None)


def test_resolve_thread_ref_unresolvable_path():
    with pytest.raises(UnsupportedUrlError):
        resolve_thread_ref('https://forums.spacebattles.com/members/someone.9/', lambda u: None)


def test_reader_url_building():
    ref = ThreadRef('forums.spacebattles.com', '12345', 'a-test-fic')
    assert ref.reader_url(1) == (
        'https://forums.spacebattles.com/threads/a-test-fic.12345/reader?threadmark_category=1')
    assert ref.reader_url(2, page=3) == (
        'https://forums.spacebattles.com/threads/a-test-fic.12345/reader/page-3?threadmark_category=2')
    assert ref.threadmarks_url.endswith('/threads/a-test-fic.12345/threadmarks')


# --- parse_threadmarks_index ---

def test_parse_threadmarks_index():
    index = parse_threadmarks_index(fixture('threadmarks_index.html'))
    assert index.title == 'A Test Fic'
    assert index.author == 'TestAuthor'
    assert index.description == 'A story about testing things.'
    assert [(c.category_id, c.name, c.count) for c in index.categories] == [
        (1, 'Threadmarks', 3),
        (2, 'Sidestory', 1),
    ]


def test_parse_threadmarks_index_no_tabs():
    html = '<h1 class="p-title-value">Plain Fic</h1>'
    index = parse_threadmarks_index(html)
    assert index.title == 'Plain Fic'
    assert index.author is None
    assert len(index.categories) == 1
    assert index.categories[0].category_id == 1


# --- parse_reader_page ---

def test_parse_reader_page_posts():
    page = parse_reader_page(fixture('reader_p1.html'))
    assert page.last_page == 2
    assert [p.post_id for p in page.posts] == ['101', '102']
    first = page.posts[0]
    assert first.threadmark_title == 'Chapter One'
    assert first.author == 'TestAuthor'
    assert first.posted_at == 1600000000
    assert 'Tuesday' in first.content_html
    assert 'threadmarkLabel' not in first.content_html  # only bbWrapper content


def test_parse_reader_page_last_page_default():
    page = parse_reader_page(fixture('reader_side_p1.html'))
    assert page.last_page == 1
    assert page.posts[0].threadmark_title == 'Omake: The Beach Episode'


# --- parse_threadmark_list (reader-less fallback source) ---

def test_parse_threadmark_list():
    listing = xenforo.parse_threadmark_list(fixture('threadmarks_list_p1.html'))
    assert listing.last_page == 2
    assert [(i.post_id, i.title, i.posted_at) for i in listing.items] == [
        ('101', 'Chapter One', 1600000000),
        ('102', 'Chapter Two', 1600100000),
    ]


def test_parse_threadmark_list_page_anchor_href():
    # hrefs come as both "/#post-N" and "/page-M#post-N" forms
    listing = xenforo.parse_threadmark_list(fixture('threadmarks_list_p2.html'))
    assert [i.post_id for i in listing.items] == ['103']
    assert listing.last_page == 2


def test_parse_threadmark_list_empty():
    listing = xenforo.parse_threadmark_list('<html><body>nothing here</body></html>')
    assert listing.items == []
    assert listing.last_page == 1


def test_threadmarks_page_and_post_urls():
    ref = ThreadRef('forum.questionablequesting.com', '38816', 'some-fic')
    assert ref.threadmarks_page_url(1) == (
        'https://forum.questionablequesting.com/threads/some-fic.38816/threadmarks?threadmark_category=1')
    assert ref.threadmarks_page_url(3, page=2).endswith('threadmarks?threadmark_category=3&page=2')
    assert ref.post_url('42').endswith('/threads/some-fic.38816/post-42')


# --- images ---

BASE = 'https://forums.spacebattles.com/threads/a-test-fic.12345/reader'


def test_extract_image_urls_precedence_and_skips():
    page = parse_reader_page(fixture('reader_p1.html'))
    urls = xenforo.extract_image_urls(page.posts[0].content_html, BASE)
    # data-url wins over the /proxy.php src; smilies skipped
    assert urls == ['https://example.com/art.png']


def test_extract_image_urls_relative_and_data_uri():
    html = '<img src="/attachments/pic.jpg"><img src="data:image/png;base64,AAAA">'
    urls = xenforo.extract_image_urls(html, BASE)
    assert urls == ['https://forums.spacebattles.com/attachments/pic.jpg']


def test_rewrite_image_srcs():
    html = ('<img src="/proxy.php?image=x" data-url="https://example.com/art.png">'
            '<img src="https://example.com/other.png">')
    out = xenforo.rewrite_image_srcs(html, BASE, {
        'https://example.com/art.png': '/api/fanfic/f1/images/abc.png',
    })
    assert 'src="/api/fanfic/f1/images/abc.png"' in out
    assert 'data-url' not in out
    # unmapped image keeps its remote URL
    assert 'src="https://example.com/other.png"' in out
