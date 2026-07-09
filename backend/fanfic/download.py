"""Fic download pipeline: fetches threadmark indexes and reader pages,
downloads embedded images, and streams chapters into the DB one reader page
at a time so a crash leaves a resumable partial fic. Progress is tracked in
an in-memory registry (same pattern as the curated-tags scan)."""

import hashlib
import threading
import time
from urllib.parse import urlparse

from backend.db.connection import get_db
from backend.fanfic import storage, xenforo
from backend.fanfic.sanitize import count_words, html_to_text, sanitize_chapter_html

# A plain browser UA: these forums sit behind Cloudflare, which challenges
# obvious bot UAs outright, and cf_clearance cookies are validated against
# the UA that solved the challenge (a browser).
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
REQUEST_DELAY = 2.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024
POSTS_PER_READER_PAGE = 10

_dl_progress: dict[str, dict] = {}
_dl_lock = threading.Lock()


class FetchBlockedError(Exception):
    pass


_BLOCKED_HINT = (
    "blocked by the site's bot protection. If this thread needs a login, "
    "paste your browser session's Cookie header in Settings → Fanfic site cookies."
)


# --- progress registry ---

def get_progress(fic_id: str) -> dict | None:
    with _dl_lock:
        p = _dl_progress.get(fic_id)
        return dict(p) if p else None


def is_active(fic_id: str) -> bool:
    with _dl_lock:
        p = _dl_progress.get(fic_id)
        return bool(p and not p.get('done'))


def start_progress(fic_id: str, phase: str) -> None:
    with _dl_lock:
        _dl_progress[fic_id] = {
            'phase': phase, 'chaptersDone': 0, 'chaptersTotal': None,
            'error': None, 'done': False,
        }


def _update_progress(fic_id: str, **kw) -> None:
    with _dl_lock:
        if fic_id in _dl_progress:
            _dl_progress[fic_id].update(kw)


def _bump_progress(fic_id: str, n: int) -> None:
    with _dl_lock:
        if fic_id in _dl_progress:
            _dl_progress[fic_id]['chaptersDone'] += n


def cancel_progress(fic_id: str) -> None:
    with _dl_lock:
        _dl_progress.pop(fic_id, None)


def _cancelled(fic_id: str) -> bool:
    with _dl_lock:
        return fic_id not in _dl_progress


# --- fetching ---

def _cookie_for(host: str) -> str | None:
    host = host.lower()
    bare = host[4:] if host.startswith('www.') else host
    row = get_db().execute(
        'SELECT cookie FROM site_cookies WHERE domain IN (?, ?)', (host, bare)
    ).fetchone()
    return row['cookie'] if row else None


def _cookies_for(url: str) -> dict | None:
    """Cookies as a dict for requests' jar. Passing them as a raw Cookie
    header would silently log us out on any redirect — requests strips
    manually-set Cookie headers when following redirects (XenForo post URLs
    always 301 to their thread page)."""
    cookie = _cookie_for(urlparse(url).netloc)
    if not cookie:
        return None
    jar: dict[str, str] = {}
    for part in cookie.split(';'):
        if '=' in part:
            name, value = part.split('=', 1)
            jar[name.strip()] = value.strip()
    return jar or None


def _headers(url: str) -> dict:
    return {'User-Agent': USER_AGENT}


def _looks_blocked(resp) -> bool:
    if resp.status_code not in (403, 503):
        return False
    if any(h.lower().startswith('cf-') for h in resp.headers):
        return True
    body = resp.text[:4000]
    return 'Just a moment' in body or 'Verifying you are human' in body


RETRY_BACKOFF = (5, 15, 30)


def _fetch(url: str):
    import requests
    # QQ rate-limits bursts with transient 403s that can outlast a short
    # pause, so back off progressively before giving up. Cloudflare
    # challenges are recognized and not retried — they need cookies, not
    # patience.
    for attempt, backoff in enumerate((*RETRY_BACKOFF, None)):
        resp = requests.get(url, timeout=20, headers=_headers(url), cookies=_cookies_for(url))
        if _looks_blocked(resp):
            raise FetchBlockedError(f'{urlparse(url).netloc} {_BLOCKED_HINT}')
        if resp.status_code in (403, 429, 503) and backoff is not None:
            print(f'Fanfic fetch got {resp.status_code} for {url}, retrying in {backoff}s')
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return resp


def _fetch_binary(url: str) -> tuple[bytes, str]:
    import requests
    with requests.get(url, timeout=30, headers=_headers(url), cookies=_cookies_for(url), stream=True) as resp:
        resp.raise_for_status()
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ValueError(f'image exceeds {MAX_IMAGE_BYTES} bytes: {url}')
            chunks.append(chunk)
        return b''.join(chunks), resp.headers.get('Content-Type', '')


# --- images ---

_EXT_FROM_CT = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/gif': '.gif', 'image/webp': '.webp', 'image/avif': '.avif',
    'image/svg+xml': '.svg', 'image/bmp': '.bmp',
}
_KNOWN_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.svg', '.bmp'}


def _ext_for(url: str, content_type: str) -> str:
    ext = _EXT_FROM_CT.get(content_type.split(';')[0].strip().lower())
    if ext:
        return ext
    from pathlib import PurePosixPath
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    return suffix if suffix in _KNOWN_EXTS else '.img'


def download_images(fic_id: str, urls: list[str]) -> dict[str, str]:
    """Download each remote image into the fic's images dir. Returns a
    url -> local-api-src mapping; failed downloads are simply omitted so
    those images keep their remote URL."""
    mapping: dict[str, str] = {}
    img_dir = storage.images_dir(fic_id)
    if img_dir is None:
        return mapping
    img_dir.mkdir(parents=True, exist_ok=True)
    for url in urls:
        stem = hashlib.sha1(url.encode()).hexdigest()[:16]
        existing = next(img_dir.glob(f'{stem}.*'), None)
        if existing:
            mapping[url] = f'/api/fanfic/{fic_id}/images/{existing.name}'
            continue
        try:
            data, content_type = _fetch_binary(url)
        except Exception as e:
            print(f'Fanfic image download failed ({url}): {e}')
            continue
        name = stem + _ext_for(url, content_type)
        (img_dir / name).write_bytes(data)
        mapping[url] = f'/api/fanfic/{fic_id}/images/{name}'
    return mapping


def process_post_html(fic_id: str, content_html: str, base_url: str) -> tuple[str, str, str | None]:
    """Download images, rewrite srcs, sanitize. Returns (clean_html, text,
    first local image filename or None)."""
    urls = xenforo.extract_image_urls(content_html, base_url)
    mapping = download_images(fic_id, urls)
    html = xenforo.rewrite_image_srcs(content_html, base_url, mapping)
    clean = sanitize_chapter_html(html)
    first_image = None
    for url in urls:
        if url in mapping:
            first_image = mapping[url].rsplit('/', 1)[-1]
            break
    return clean, html_to_text(clean), first_image


# --- import / update jobs ---

def _insert_chapter(db, fic_id: str, category: str, position: int,
                    post: xenforo.ReaderPost, source_url: str,
                    clean_html: str, text: str) -> bool:
    from ulid import ULID
    cur = db.execute(
        'INSERT OR IGNORE INTO fic_chapters'
        '(id, fic_id, position, title, category, content_html, content_text,'
        ' source_url, source_post_id, word_count, posted_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (str(ULID()), fic_id, position, post.threadmark_title or f'Chapter {position}',
         category, clean_html, text, source_url, post.post_id,
         count_words(text), post.posted_at, int(time.time())),
    )
    return cur.rowcount > 0


def _finalize_fic(db, fic_id: str, cover: str | None) -> None:
    agg = db.execute(
        'SELECT COUNT(*) AS n, COALESCE(SUM(word_count), 0) AS words'
        ' FROM fic_chapters WHERE fic_id=?', (fic_id,)).fetchone()
    now = int(time.time())
    db.execute(
        'UPDATE fics SET chapter_count=?, word_count=?, download_status=?,'
        ' download_error=NULL, last_checked_at=?, updated_at=?,'
        ' cover_path=COALESCE(cover_path, ?) WHERE id=?',
        (agg['n'], agg['words'], 'complete', now, now, cover, fic_id),
    )
    db.commit()


def _fail_fic(fic_id: str, error: str) -> None:
    db = get_db()
    db.execute(
        'UPDATE fics SET download_status=?, download_error=?, updated_at=? WHERE id=?',
        ('error', error, int(time.time()), fic_id),
    )
    db.commit()
    _update_progress(fic_id, phase='error', error=error, done=True)


def _walk_category(db, fic_id: str, ref: xenforo.ThreadRef,
                   cat: xenforo.ThreadmarkCategory, start_position: int,
                   start_page: int = 1,
                   known_post_ids: set[str] | None = None) -> tuple[int, str | None]:
    """Download one threadmark category's chapters. Tries the reader view
    (~10 chapters per request); when the reader is unavailable (QQ forbids
    it) falls back to harvesting posts from the thread pages themselves.
    Returns (number of new chapters, author of the first post)."""
    known_post_ids = known_post_ids if known_post_ids is not None else set()
    inserted, first_author = _walk_category_reader(
        db, fic_id, ref, cat, start_position, start_page, known_post_ids)
    if inserted == 0 and not _cancelled(fic_id):
        inserted, first_author = _walk_category_via_thread(
            db, fic_id, ref, cat, start_position, known_post_ids)
    return inserted, first_author


def _walk_category_reader(db, fic_id: str, ref: xenforo.ThreadRef,
                          cat: xenforo.ThreadmarkCategory, start_position: int,
                          start_page: int, known_post_ids: set[str]) -> tuple[int, str | None]:
    """Page through one threadmark category's reader, inserting chapters.
    Committed per reader page."""
    position = start_position
    inserted = 0
    first_author: str | None = None
    page = start_page
    while True:
        if _cancelled(fic_id):
            return inserted, first_author
        try:
            resp = _fetch(ref.reader_url(cat.category_id, page))
        except FetchBlockedError:
            raise
        except Exception:
            # Reader unavailable (QQ 403s it, or an empty category 404s):
            # report zero so the caller falls back to the thread-page walk.
            if page == start_page:
                return inserted, first_author
            raise
        reader = xenforo.parse_reader_page(resp.text)
        if not reader.posts:
            return inserted, first_author
        if first_author is None and reader.posts:
            first_author = reader.posts[0].author
        for post in reader.posts:
            if post.post_id in known_post_ids:
                continue
            clean, text, _ = process_post_html(fic_id, post.content_html, str(resp.url))
            position += 1
            source_url = f'{ref.thread_url}post-{post.post_id}'
            if _insert_chapter(db, fic_id, cat.name, position, post, source_url, clean, text):
                inserted += 1
                known_post_ids.add(post.post_id)
            else:
                position -= 1
        db.commit()
        _bump_progress(fic_id, len(reader.posts))
        if page >= reader.last_page:
            return inserted, first_author
        page += 1


def _collect_threadmark_items(fic_id: str, ref: xenforo.ThreadRef,
                              cat: xenforo.ThreadmarkCategory) -> list[xenforo.ThreadmarkItem]:
    """Gather the ordered chapter list from the (paginated) threadmarks
    index pages of one category."""
    items: list[xenforo.ThreadmarkItem] = []
    seen: set[str] = set()
    page = 1
    while True:
        if _cancelled(fic_id):
            return items
        listing = xenforo.parse_threadmark_list(
            _fetch(ref.threadmarks_page_url(cat.category_id, page)).text)
        for item in listing.items:
            if item.post_id not in seen:
                seen.add(item.post_id)
                items.append(item)
        if page >= listing.last_page or not listing.items:
            return items
        page += 1


def _walk_category_via_thread(db, fic_id: str, ref: xenforo.ThreadRef,
                              cat: xenforo.ThreadmarkCategory, start_position: int,
                              known_post_ids: set[str]) -> tuple[int, str | None]:
    """Reader-less fallback: list the category's chapters from the
    threadmarks index, then walk the thread pages that contain them. Each
    post URL redirects to its thread page, whose parsed posts are cached so
    every page is fetched once."""
    items = [i for i in _collect_threadmark_items(fic_id, ref, cat)
             if i.post_id not in known_post_ids]
    if not items:
        return 0, None

    harvested: dict[str, xenforo.ReaderPost] = {}
    harvested_meta: dict[str, str] = {}  # post_id -> base_url of its page
    position = start_position
    inserted = 0
    first_author: str | None = None

    for item in items:
        if _cancelled(fic_id):
            return inserted, first_author
        if item.post_id not in harvested:
            resp = _fetch(ref.post_url(item.post_id))
            page = xenforo.parse_reader_page(resp.text)
            wanted = {i.post_id for i in items}
            for post in page.posts:
                if post.post_id in wanted and post.post_id not in harvested:
                    harvested[post.post_id] = post
                    harvested_meta[post.post_id] = str(resp.url)
        post = harvested.get(item.post_id)
        if post is None:
            print(f'Fanfic thread-walk: post {item.post_id} not found on its page, skipping')
            continue
        if first_author is None:
            first_author = post.author
        if not post.threadmark_title:
            post.threadmark_title = item.title
        if post.posted_at is None:
            post.posted_at = item.posted_at
        clean, text, _ = process_post_html(fic_id, post.content_html, harvested_meta[item.post_id])
        position += 1
        source_url = f'{ref.thread_url}post-{post.post_id}'
        if _insert_chapter(db, fic_id, cat.name, position, post, source_url, clean, text):
            inserted += 1
            known_post_ids.add(post.post_id)
            db.commit()
            _bump_progress(fic_id, 1)
        else:
            position -= 1
    return inserted, first_author


def run_import(fic_id: str, ref: xenforo.ThreadRef) -> None:
    db = get_db()
    try:
        _update_progress(fic_id, phase='index')
        index = xenforo.parse_threadmarks_index(_fetch(ref.threadmarks_url).text)
        now = int(time.time())
        db.execute(
            'UPDATE fics SET title=?, author=?, description=?, updated_at=? WHERE id=?',
            (index.title or ref.slug or 'Untitled', index.author, index.description, now, fic_id),
        )
        db.commit()

        counts = [c.count for c in index.categories]
        total = sum(counts) if all(c is not None for c in counts) else None
        _update_progress(fic_id, phase='chapters', chaptersTotal=total)

        imported = 0
        author = index.author
        for cat in index.categories:
            n, first_author = _walk_category(db, fic_id, ref, cat, start_position=0)
            imported += n
            # The threadmarks index rarely names the author; the first
            # threadmarked post's author is the fic author in practice.
            if author is None and first_author:
                author = first_author
            if _cancelled(fic_id):
                return

        if imported == 0:
            _fail_fic(fic_id, 'No threadmarked chapters found — does this thread have threadmarks?')
            return

        if author and not index.author:
            db.execute('UPDATE fics SET author=? WHERE id=?', (author, fic_id))
        _finalize_fic(db, fic_id, _first_local_image(db, fic_id))
        _update_progress(fic_id, phase='done', done=True)
    except Exception as e:
        print(f'Fanfic import failed for {fic_id}: {e}')
        _fail_fic(fic_id, str(e))


def _first_local_image(db, fic_id: str) -> str | None:
    row = db.execute(
        'SELECT content_html FROM fic_chapters WHERE fic_id=?'
        ' AND content_html LIKE ?'
        " ORDER BY CASE WHEN LOWER(category) IN ('threadmarks','chapters') THEN 0 ELSE 1 END,"
        ' position LIMIT 1',
        (fic_id, f'%/api/fanfic/{fic_id}/images/%'),
    ).fetchone()
    if not row:
        return None
    import re
    m = re.search(rf'/api/fanfic/{fic_id}/images/([A-Za-z0-9._-]+)', row['content_html'])
    return m.group(1) if m else None


def run_check_updates(fic_id: str) -> None:
    db = get_db()
    row = db.execute('SELECT source_url FROM fics WHERE id=?', (fic_id,)).fetchone()
    if not row or not row['source_url']:
        _update_progress(fic_id, phase='error', error='Not a forum fic', done=True)
        return
    ref = xenforo.parse_thread_ref(row['source_url'])
    if not ref:
        _update_progress(fic_id, phase='error', error='Stored source URL is not a thread URL', done=True)
        return
    try:
        _update_progress(fic_id, phase='updating')
        index = xenforo.parse_threadmarks_index(_fetch(ref.threadmarks_url).text)
        # Refresh metadata too — a fic whose first import failed early may
        # still carry its placeholder title.
        now = int(time.time())
        if index.title:
            db.execute('UPDATE fics SET title=?, updated_at=? WHERE id=?',
                       (index.title, now, fic_id))
        if index.author:
            db.execute('UPDATE fics SET author=? WHERE id=?', (index.author, fic_id))
        if index.description:
            db.execute('UPDATE fics SET description=? WHERE id=?', (index.description, fic_id))
        db.commit()

        author = index.author
        for cat in index.categories:
            stats = db.execute(
                'SELECT COUNT(*) AS n, COALESCE(MAX(position), 0) AS maxpos'
                ' FROM fic_chapters WHERE fic_id=? AND category=?',
                (fic_id, cat.name)).fetchone()
            known = {
                r['source_post_id'] for r in db.execute(
                    'SELECT source_post_id FROM fic_chapters WHERE fic_id=? AND category=?',
                    (fic_id, cat.name)).fetchall()
            }
            if cat.count is not None and cat.count <= stats['n']:
                continue
            start_page = max(1, -(-stats['n'] // POSTS_PER_READER_PAGE))
            _, first_author = _walk_category(
                db, fic_id, ref, cat, start_position=stats['maxpos'],
                start_page=start_page, known_post_ids=known)
            if author is None and first_author:
                author = first_author
            if _cancelled(fic_id):
                return

        total = db.execute('SELECT COUNT(*) AS n FROM fic_chapters WHERE fic_id=?',
                           (fic_id,)).fetchone()['n']
        if total == 0:
            _fail_fic(fic_id, 'No threadmarked chapters found — does this thread have threadmarks?')
            return
        if author and not index.author:
            db.execute('UPDATE fics SET author=COALESCE(author, ?) WHERE id=?', (author, fic_id))
        _finalize_fic(db, fic_id, _first_local_image(db, fic_id))
        _update_progress(fic_id, phase='done', done=True)
    except Exception as e:
        print(f'Fanfic update check failed for {fic_id}: {e}')
        _fail_fic(fic_id, str(e))
