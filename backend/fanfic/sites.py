"""Non-forum library sources. URL and HTML parsing is independent of IO."""

import json
import re
from html import escape
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlencode

from bs4 import BeautifulSoup

DOMAINS = {'fanfiction.net', 'archiveofourown.org', 'patreon.com'}
SOURCE_TYPES = {'fanfiction', 'ao3', 'patreon'}


@dataclass(frozen=True)
class WorkRef:
    site: str
    source_type: str
    id: str
    url: str
    favorited_at: int | None = None
    followed_at: int | None = None


def parse_work_url(url: str) -> WorkRef | None:
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port:
        return None
    host = (p.hostname or '').removeprefix('www.')
    if host == 'm.fanfiction.net':
        host = 'fanfiction.net'
    patterns = {
        'fanfiction.net': (r'/s/(\d+)(?:/|$)', 'fanfiction', 'https://www.fanfiction.net/s/{}/1/'),
        'archiveofourown.org': (r'/works/(\d+)(?:/|$)', 'ao3', 'https://archiveofourown.org/works/{}'),
        'patreon.com': (r'/posts/(?:[^/]*-)?(\d+)/?$', 'patreon', 'https://www.patreon.com/posts/{}'),
    }
    if host not in patterns:
        return None
    pattern, kind, template = patterns[host]
    match = re.match(pattern, p.path)
    return WorkRef(host, kind, match[1], template.format(match[1])) if match else None


def same_site_url(url: str, base: str) -> str:
    absolute = urljoin(base, url)
    p, b = urlparse(absolute), urlparse(base)
    if (p.scheme != 'https' or p.hostname != b.hostname or p.port
            or p.username or p.password):
        raise ValueError('The site returned a link outside the expected host')
    return absolute


def _text(node) -> str:
    return node.get_text(' ', strip=True) if node else ''


def parse_ffn(html: str, ref: WorkRef, chapter: int = 1):
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('#storytext')
    title = soup.select_one('#profile_top b.xcontrast_txt, #profile_top b, #content_wrapper_inner b')
    if content is None or title is None:
        raise ValueError('Story text unavailable; the story may be removed or the session blocked')
    options = soup.select('select[name=chapter] option') or soup.select('#chap_select option')
    chapters = {int(o['value']): _text(o) for o in options if str(o.get('value', '')).isdigit()}
    return {
        'title': _text(title), 'author': _text(soup.select_one('#profile_top a[href^="/u/"]')),
        'description': _text(soup.select_one('#profile_top div.xcontrast_txt')),
        'total': max(chapters, default=1),
        'chapters': [(str(chapter), chapters.get(chapter, _text(title)), str(content), None)],
    }


def parse_ao3(html: str, ref: WorkRef):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.select_one('#workskin .preface h2.title')
    if title is None:
        raise ValueError('AO3 work unavailable; sign in for restricted works or check whether it was removed')
    chapters = []
    for index, node in enumerate(soup.select('#chapters > .chapter'), 1):
        content = node.select_one('.userstuff[role=article]')
        if content is None:
            raise ValueError('AO3 returned a chapter without its text')
        link = node.select_one('.title a[href*="/chapters/"]')
        match = re.search(r'/chapters/(\d+)', link.get('href', '')) if link else None
        key = match[1] if match else f'position-{index}'
        chapters.append((key, _text(node.select_one('.title')) or f'Chapter {index}', str(content), None))
    if not chapters:
        content = soup.select_one('#chapters > .userstuff')
        if content is not None:
            chapters.append(('position-1', _text(title), str(content), None))
    if not chapters:
        raise ValueError('AO3 returned no readable chapters')
    return {'title': _text(title), 'author': _text(soup.select_one('#workskin .byline')),
            'description': _text(soup.select_one('#workskin .summary .userstuff')),
            'total': len(chapters), 'chapters': chapters}


PATREON_FIELDS = {'include': 'user', 'fields[post]':
                  'title,content,content_json_string,current_user_can_view,published_at,url',
                  'fields[user]': 'full_name'}


def patreon_api(path: str, **params) -> str:
    return 'https://www.patreon.com/api/' + path + '?' + urlencode({**PATREON_FIELDS, **params})


def parse_patreon(data: dict):
    post = data.get('data')
    if not isinstance(post, dict) or post.get('type') != 'post':
        raise ValueError('Patreon did not return a post')
    a = post.get('attributes', {})
    if a.get('current_user_can_view') is not True:
        raise ValueError('This Patreon post is locked or the session has expired')
    content = a.get('content')
    if not content and a.get('content_json_string'):
        content = _rich_text(json.loads(a['content_json_string']))
    if not content:
        raise ValueError('This Patreon post has no downloadable text (media-only posts are not supported)')
    user_id = post.get('relationships', {}).get('user', {}).get('data', {}).get('id')
    author = next((x.get('attributes', {}).get('full_name', '') for x in data.get('included', [])
                   if x.get('type') == 'user' and x.get('id') == user_id), '')
    posted = a.get('published_at')
    timestamp = int(datetime.fromisoformat(posted.replace('Z', '+00:00')).timestamp()) if posted else None
    return {'title': a.get('title') or 'Untitled post', 'author': author, 'description': '',
            'total': 1, 'chapters': [(str(post['id']), a.get('title') or 'Post', content, timestamp)]}


def _rich_text(node: dict, depth: int = 0) -> str:
    """Patreon's structured text posts use document/paragraph/text nodes.

    Escape text and attributes before the shared chapter sanitizer; unknown
    containers preserve their children rather than discarding the prose.
    """
    if depth > 100 or not isinstance(node, dict):
        raise ValueError('Invalid Patreon text document')
    kind = node.get('type')
    if kind == 'text':
        text = escape(node.get('text', ''))
        for mark in node.get('marks', []):
            tag = {'bold': 'strong', 'italic': 'em', 'strike': 's', 'code': 'code'}.get(mark.get('type'))
            if tag:
                text = f'<{tag}>{text}</{tag}>'
            elif mark.get('type') == 'link':
                href = escape(mark.get('attrs', {}).get('href', ''), quote=True)
                text = f'<a href="{href}">{text}</a>'
        return text
    if kind == 'hardBreak':
        return '<br>'
    if kind == 'horizontalRule':
        return '<hr>'
    children = ''.join(_rich_text(child, depth + 1) for child in node.get('content', []))
    tag = {'paragraph': 'p', 'heading': 'h3', 'bulletList': 'ul', 'orderedList': 'ol',
           'listItem': 'li', 'blockquote': 'blockquote', 'codeBlock': 'pre'}.get(kind)
    return f'<{tag}>{children}</{tag}>' if tag else children


def collection_urls(site: str, collection: str, username: str = '') -> list[str]:
    if site == 'fanfiction.net':
        choices = {'favorites': ['https://www.fanfiction.net/favorites/story.php'],
                   'follows': ['https://www.fanfiction.net/alert/story.php']}
    elif site == 'archiveofourown.org':
        if not re.fullmatch(r'[A-Za-z0-9_-]+', username):
            raise ValueError('Enter your AO3 username')
        base = f'https://archiveofourown.org/users/{username}'
        choices = {'bookmarks': [base + '/bookmarks'],
                   'subscriptions': [base + '/subscriptions?type=works']}
    elif site == 'patreon.com':
        choices = {'feed': [patreon_api('stream', **{'filter[is_following]': 'true',
                                                   'json-api-use-default-includes': 'false'})]}
    else:
        raise ValueError('Unsupported collection site')
    if collection == 'all':
        return [url for urls in choices.values() for url in urls]
    if collection not in choices:
        raise ValueError('Unsupported collection for this site')
    return choices[collection]


def parse_collection(html: str, url: str) -> tuple[list[WorkRef], str | None]:
    """Only list entries, never recommendation/navigation links elsewhere."""
    soup = BeautifulSoup(html, 'html.parser')
    if soup.select_one('input[type=password]'):
        raise ValueError('Session expired; save fresh request headers in Settings → Fanfic site cookies')
    host = urlparse(url).hostname
    if host == 'archiveofourown.org':
        container = soup.select_one('ol.bookmark.index, dl.subscription.index')
        links = container.select('h4.heading a, dt a') if container else []
    else:
        container = soup.select_one('#gui_table1, #content_wrapper_inner')
        links = container.select('a[href]') if container else []
    if container is None:
        raise ValueError('Collection list not found; check your session and the collection URL')
    refs = {}
    for a in links:
        ref = parse_work_url(urljoin(url, a.get('href', '')))
        if ref and ref.site == host.removeprefix('www.'):
            if ref.site == 'fanfiction.net':
                added = _list_added_at(a)
                if urlparse(url).path == '/favorites/story.php':
                    ref = replace(ref, favorited_at=added)
                elif urlparse(url).path == '/alert/story.php':
                    ref = replace(ref, followed_at=added)
                old = refs.get(ref.url)
                if old:
                    ref = replace(ref, favorited_at=ref.favorited_at or old.favorited_at,
                                  followed_at=ref.followed_at or old.followed_at)
            refs[ref.url] = ref
    next_link = soup.select_one('a[rel=next], .pagination .next a, a.next_page')
    if next_link is None:
        next_link = next((a for a in soup.select('a[href]')
                          if _text(a).lower().strip(' »›>') == 'next'), None)
    next_url = same_site_url(next_link['href'], url) if next_link else None
    if next_url and urlparse(next_url).path != urlparse(url).path:
        raise ValueError('Unexpected collection pagination path')
    return list(refs.values()), next_url


def _parse_added_date(node):
    """Read only an explicitly identified addition-date field."""
    timestamp = node.get('data-xutime')
    if timestamp is None:
        stamp = node.select_one('[data-xutime]')
        timestamp = stamp.get('data-xutime') if stamp else None
    if timestamp is not None:
        try:
            value = int(timestamp)
            return value if 0 < value <= 253402300799 else None
        except (TypeError, ValueError):
            return None
    raw = node.get('datetime') or _text(node)
    raw = re.sub(r'^(?:Date\s+)?(?:Added|Favorited|Followed)(?:\s+on)?\s*:?\s*', '', raw, flags=re.I)
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%m-%d-%y', '%Y-%m-%d', '%b %d, %Y'):
        try:
            return int(datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


def _list_added_at(link):
    row = link.find_parent('tr')
    if row is None:
        return None
    cells = row.find_all(['td', 'th'], recursive=False)
    # Prefer a Date Added column; published/updated timestamps elsewhere in
    # the same story row must never be mistaken for the user's history.
    table = row.find_parent('table')
    if table:
        for header in table.find_all('tr'):
            if header.find_parent('table') is not table:
                continue
            labels = header.find_all(['td', 'th'], recursive=False)
            for index, label in enumerate(labels):
                if re.fullmatch(r'(?:date\s+)?(?:added|favorited|followed)', _text(label), re.I):
                    if index < len(cells):
                        return _parse_added_date(cells[index])
    for cell in cells:
        if re.match(r'^(?:Date\s+)?(?:Added|Favorited|Followed)(?:\s+on)?\s*:', _text(cell), re.I):
            return _parse_added_date(cell)
        for node in cell.select('[title], [data-label]'):
            label = node.get('data-label') or node.get('title', '')
            if re.fullmatch(r'(?:date\s+)?(?:added|favorited|followed)', label, re.I):
                return _parse_added_date(node)
    # FF.net's account lists have six columns: story, author, category,
    # updated, added, remove. Public profile story cards do not use this layout.
    if (table and table.get('id') == 'gui_table1' and len(cells) == 6
            and cells[0].find('a', href=re.compile(r'/s/\d+/'))
            and cells[1].find('a', href=re.compile(r'/u/\d+/'))):
        return _parse_added_date(cells[4])
    return None


def parse_patreon_collection(data: dict, url: str):
    if not isinstance(data.get('data'), list):
        raise ValueError('Patreon feed unavailable; check your session')
    refs, skipped = [], 0
    for post in data['data']:
        if post.get('type') != 'post':
            continue
        if post.get('attributes', {}).get('current_user_can_view') is not True:
            skipped += 1
            continue
        if not str(post.get('id', '')).isdigit():
            raise ValueError('Invalid Patreon post ID')
        refs.append(parse_work_url('https://www.patreon.com/posts/' + str(post['id'])))
    next_url = data.get('links', {}).get('next')
    if next_url:
        next_url = same_site_url(next_url, url)
        if urlparse(next_url).path != urlparse(url).path:
            raise ValueError('Unexpected Patreon pagination path')
    return refs, next_url, skipped
