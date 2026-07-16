"""Pure parsers for XenForo 2 forums (SpaceBattles / Sufficient Velocity /
Questionable Questing). No network and no DB access — every function takes
strings and returns dataclasses so tests can feed fixture HTML."""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

KNOWN_SITES = {
    'forums.spacebattles.com',
    'forums.sufficientvelocity.com',
    'forum.questionablequesting.com',
}

DEFAULT_CATEGORY_ID = 1
DEFAULT_CATEGORY_NAME = 'threadmarks'


class UnsupportedUrlError(ValueError):
    pass


@dataclass
class ThreadRef:
    domain: str
    thread_id: str
    slug: str

    @property
    def thread_url(self) -> str:
        return f'https://{self.domain}/threads/{self.slug}.{self.thread_id}/'

    @property
    def threadmarks_url(self) -> str:
        return f'https://{self.domain}/threads/{self.slug}.{self.thread_id}/threadmarks'

    def reader_url(self, category_id: int, page: int = 1) -> str:
        base = f'https://{self.domain}/threads/{self.slug}.{self.thread_id}/reader'
        if page > 1:
            base += f'/page-{page}'
        return f'{base}?threadmark_category={category_id}'

    def threadmarks_page_url(self, category_id: int, page: int = 1) -> str:
        return (f'{self.threadmarks_url}?threadmark_category={category_id}'
                + (f'&page={page}' if page > 1 else ''))

    def post_url(self, post_id: str) -> str:
        return f'https://{self.domain}/threads/{self.slug}.{self.thread_id}/post-{post_id}'


@dataclass
class ThreadmarkCategory:
    category_id: int
    name: str
    count: int | None = None


@dataclass
class ThreadIndex:
    title: str
    author: str | None
    description: str | None
    categories: list[ThreadmarkCategory] = field(default_factory=list)


@dataclass
class ReaderPost:
    post_id: str
    threadmark_title: str
    author: str | None
    posted_at: int | None
    content_html: str


@dataclass
class ReaderPage:
    posts: list[ReaderPost]
    last_page: int


@dataclass
class ThreadmarkItem:
    post_id: str
    title: str
    posted_at: int | None


@dataclass
class ThreadmarkListPage:
    items: list[ThreadmarkItem]
    last_page: int


def _normalize_host(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith('www.') else host


_THREAD_PATH = re.compile(r'^/threads/(?:([^/]*?)\.)?(\d+)(?:/.*)?$')


def parse_thread_ref(url: str) -> ThreadRef | None:
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc)
    if host not in KNOWN_SITES:
        return None
    m = _THREAD_PATH.match(parsed.path)
    if not m:
        return None
    slug, thread_id = m.group(1) or '', m.group(2)
    return ThreadRef(domain=host, thread_id=thread_id, slug=slug)


def resolve_thread_ref(url: str, fetch) -> ThreadRef:
    """Resolve any pasted URL to a ThreadRef. `fetch(url)` is only called for
    post/goto URLs, which XenForo redirects to the canonical thread URL."""
    ref = parse_thread_ref(url)
    if ref:
        return ref
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc)
    if host not in KNOWN_SITES:
        raise UnsupportedUrlError(
            'Unsupported site. Supported: ' + ', '.join(sorted(KNOWN_SITES)))
    if re.match(r'^/(posts|goto)/', parsed.path):
        resp = fetch(url)
        ref = parse_thread_ref(resp.url)
        if ref:
            return ref
    raise UnsupportedUrlError('Could not find a thread in that URL')


def _tag_text(tag) -> str:
    return re.sub(r'\s+', ' ', tag.get_text(' ', strip=True)) if tag else ''


def parse_threadmarks_index(html: str) -> ThreadIndex:
    soup = BeautifulSoup(html, 'html.parser')

    title = _tag_text(soup.select_one('h1.p-title-value'))
    # The threadmarks page titles itself "Threadmarks for: <thread title>"
    title = re.sub(r'^Threadmarks\s*(for|in)?\s*:?\s*', '', title, flags=re.I).strip() or title

    author_tag = soup.select_one('.p-description a.username')
    author = _tag_text(author_tag) or None

    desc_tag = soup.find('meta', attrs={'property': 'og:description'})
    description = (desc_tag.get('content') or '').strip() or None if desc_tag else None

    # Per-category counts from the tab panes' "Statistics (N threadmarks, …)"
    # headers (only present for panes the server rendered — usually just the
    # active one).
    counts: dict[int, int] = {}
    for li in soup.select('li[aria-labelledby^="threadmark-category-"]'):
        m = re.match(r'threadmark-category-(\d+)', li.get('aria-labelledby', ''))
        s = re.search(r'Statistics \(([\d,]+) threadmarks?', li.get_text())
        if m and s:
            counts[int(m.group(1))] = int(s.group(1).replace(',', ''))

    # Category tabs. The default category's tab has no threadmark_category
    # query param; the RSS control also carries the param and must be skipped.
    categories: list[ThreadmarkCategory] = []
    seen: set[int] = set()
    tabs = soup.select('.block-tabHeader--threadmarkCategoryTabs a.tabs-tab') \
        or soup.select('a.tabs-tab[role="tab"]')
    for a in tabs:
        href = a.get('href', '')
        if '.rss' in href:
            continue
        raw = parse_qs(urlparse(href).query).get('threadmark_category', [''])[0]
        cid = int(raw) if raw.isdigit() else DEFAULT_CATEGORY_ID
        if cid in seen:
            continue
        seen.add(cid)
        name = _tag_text(a) or DEFAULT_CATEGORY_NAME
        categories.append(ThreadmarkCategory(category_id=cid, name=name, count=counts.get(cid)))

    if not categories:
        categories = [ThreadmarkCategory(
            DEFAULT_CATEGORY_ID, DEFAULT_CATEGORY_NAME, counts.get(DEFAULT_CATEGORY_ID))]

    return ThreadIndex(title=title, author=author, description=description, categories=categories)


@dataclass
class AlertItem:
    ref: ThreadRef
    alert_at: int | None  # unix seconds from the alert row's <time data-time>


def parse_alerts(html: str, domain: str) -> list[AlertItem]:
    """Thread references from an /account/alerts page. Each alert row links
    to its content; only rows with a direct thread link are kept — resolving
    bare /posts/ links (reply/quote alerts) would cost a request each.
    Duplicates are preserved in page order; callers dedupe (newest
    timestamp wins)."""
    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.select('li.js-alert') or soup.select('[data-alert-id]')
    base = f'https://{domain}/'
    items: list[AlertItem] = []
    for row in rows:
        ref = None
        for a in row.select('a[href]'):
            ref = parse_thread_ref(urljoin(base, a['href']))
            if ref:
                break
        if not ref:
            continue
        time_tag = row.select_one('time[data-time]')
        alert_at = None
        if time_tag and str(time_tag.get('data-time', '')).isdigit():
            alert_at = int(time_tag['data-time'])
        items.append(AlertItem(ref=ref, alert_at=alert_at))
    return items


def parse_thread_tags(html: str) -> list[str]:
    """Tags from a thread page's tag list. XenForo 2 renders them as
    <a class="tagItem"> anchors, either wrapped in a span.js-tagList or
    directly inside a .tagList container; the bare a.tagItem fallback covers
    customized themes (QQ). Returns display names in page order, deduped
    case-insensitively."""
    soup = BeautifulSoup(html, 'html.parser')
    anchors = soup.select('.js-tagList .tagItem, .tagList .tagItem') or soup.select('a.tagItem')
    tags: list[str] = []
    seen: set[str] = set()
    for a in anchors:
        name = _tag_text(a)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            tags.append(name)
    return tags


def _last_page(soup) -> int:
    last = 1
    for a in soup.select('.pageNav-main .pageNav-page'):
        text = _tag_text(a)
        if text.isdigit():
            last = max(last, int(text))
    return last


_POST_ID_IN_HREF = re.compile(r'(?:#|/)post-(\d+)')


def parse_threadmark_list(html: str) -> ThreadmarkListPage:
    """Parse the threadmarks *index* page's list of chapters. Used as the
    fallback source of chapter post ids when the reader view is unavailable
    (QuestionableQuesting forbids /reader)."""
    soup = BeautifulSoup(html, 'html.parser')
    items: list[ThreadmarkItem] = []
    for row in soup.select('.structItem--threadmark'):
        a = row.select_one('.structItem-title a[href]')
        if not a:
            continue
        m = _POST_ID_IN_HREF.search(a.get('href', ''))
        if not m:
            continue
        time_tag = row.select_one('time[data-time]')
        posted_at = None
        if time_tag and str(time_tag.get('data-time', '')).isdigit():
            posted_at = int(time_tag['data-time'])
        items.append(ThreadmarkItem(post_id=m.group(1), title=_tag_text(a), posted_at=posted_at))
    return ThreadmarkListPage(items=items, last_page=_last_page(soup))


def parse_reader_page(html: str) -> ReaderPage:
    soup = BeautifulSoup(html, 'html.parser')

    posts: list[ReaderPost] = []
    for article in soup.select('article.message'):
        content_ref = article.get('data-content', '')
        if not content_ref.startswith('post-'):
            continue
        post_id = content_ref[len('post-'):]

        label = article.select_one('span.threadmarkLabel, .threadmarkLabel')
        body = article.select_one('div.bbWrapper')
        if body is None:
            continue

        time_tag = article.select_one('time.u-dt[data-time]') or article.select_one('time[data-time]')
        posted_at = None
        if time_tag and str(time_tag.get('data-time', '')).isdigit():
            posted_at = int(time_tag['data-time'])

        posts.append(ReaderPost(
            post_id=post_id,
            threadmark_title=_tag_text(label),
            author=article.get('data-author') or None,
            posted_at=posted_at,
            content_html=body.decode_contents(),
        ))

    return ReaderPage(posts=posts, last_page=_last_page(soup))


_SKIP_IMAGE = re.compile(r'/smilies/|/reaction/|^data:', re.I)


def _image_src(img, base_url: str) -> str | None:
    for attr in ('data-url', 'data-src', 'src'):
        val = (img.get(attr) or '').strip()
        if val and not _SKIP_IMAGE.search(val):
            return urljoin(base_url, val)
    return None


@dataclass
class ImageSource:
    url: str                    # preferred remote URL (the post's original image)
    proxy_url: str | None = None  # the forum's /proxy.php cached copy, if present


def extract_image_sources(content_html: str, base_url: str) -> list[ImageSource]:
    """Image URLs to download, in document order. XenForo's image proxy
    rewrites remote images to src="/proxy.php?image=<orig>&hash=..." with the
    original URL in data-url; the original is preferred but the proxy copy is
    kept as a fallback — the forum's cache often outlives the original (e.g.
    expiring Discord attachment links)."""
    soup = BeautifulSoup(content_html, 'html.parser')
    sources: list[ImageSource] = []
    seen: set[str] = set()
    for img in soup.find_all('img'):
        src = _image_src(img, base_url)
        if not src or not src.startswith(('http://', 'https://')) or src in seen:
            continue
        seen.add(src)
        proxy = None
        for attr in ('src', 'data-src'):
            val = (img.get(attr) or '').strip()
            if val and 'proxy.php' in val:
                proxy = urljoin(base_url, val)
                break
        if proxy == src:
            proxy = None
        sources.append(ImageSource(url=src, proxy_url=proxy))
    return sources


def extract_image_urls(content_html: str, base_url: str) -> list[str]:
    return [s.url for s in extract_image_sources(content_html, base_url)]


def rewrite_image_srcs(content_html: str, base_url: str, mapping: dict[str, str]) -> str:
    """Point img tags at locally stored copies. `mapping` maps the resolved
    remote URL (as returned by extract_image_urls) to the new src. Unmapped
    images keep their absolute remote URL so they still render online."""
    soup = BeautifulSoup(content_html, 'html.parser')
    for img in soup.find_all('img'):
        src = _image_src(img, base_url)
        if not src:
            continue
        img['src'] = mapping.get(src, src)
        for attr in ('data-url', 'data-src', 'srcset'):
            if attr in img.attrs:
                del img[attr]
    return str(soup)
