"""Fic download pipeline: fetches threadmark indexes and reader pages,
downloads embedded images, and streams chapters into the DB one reader page
at a time so a crash leaves a resumable partial fic. Progress is tracked in
an in-memory registry (same pattern as the curated-tags scan)."""

import hashlib
import re
import threading
import time
from typing import NamedTuple
from urllib.parse import urlparse

from ulid import ULID

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
    always 301 to their thread page).

    /api/fanfic/cookies rejects non-ASCII input outright, but a cookie saved
    before that check existed can still be sitting in the DB — without this,
    it reaches requests' header encoder and comes back as a raw
    'latin-1' codec can't encode character ... UnicodeEncodeError instead of
    a message that says what to do about it."""
    cookie = _cookie_for(urlparse(url).netloc)
    if not cookie:
        return None
    bad = next((c for c in cookie if ord(c) > 127), None)
    if bad is not None:
        domain = urlparse(url).netloc
        raise FetchBlockedError(
            f'{domain}: stored cookie contains a non-ASCII character ({bad!r}) left over '
            "from a truncated copy-paste (commonly cf_clearance cut short by a '…'). "
            'Paste a fresh Cookie header in Settings → Fanfic site cookies.')
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


def fetch_alerts(domain: str) -> list[xenforo.AlertItem]:
    """Fetch and parse page 1 of a site's /account/alerts. Requires a stored
    session cookie — guests are redirected to the login page, which is
    reported as a blocked fetch rather than an empty alert list."""
    resp = _fetch(f'https://{domain}/account/alerts')
    items = xenforo.parse_alerts(resp.text, domain)
    logged_out = '/login' in urlparse(str(resp.url)).path or \
        (not items and '/login/login' in resp.text)
    if logged_out:
        raise FetchBlockedError(
            f'{domain}: not logged in — paste a fresh Cookie header in'
            ' Settings → Fanfic site cookies.')
    return items


# --- watched-threads archive scan ---
#
# Rare, user-triggered background job that walks a site's /watched/threads
# listing (every thread the account follows, not just recent alert activity)
# and queues anything missing from the library, so old/dead watched threads
# that never generate a fresh alert still get archived. Reuses the same
# placeholder-fic + serial drain-worker pipeline as refresh_alerts. Progress
# is an in-memory registry like _dl_progress, but the page position and
# running counts are also checkpointed to fanfic_watched_scans so a Flask
# restart mid-scan resumes instead of starting over from page 1.

_watch_scan_progress: dict[str, dict] = {}
_watch_scan_lock = threading.Lock()


def get_watched_scan_progress(domain: str) -> dict | None:
    with _watch_scan_lock:
        p = _watch_scan_progress.get(domain)
        return dict(p) if p else None


def is_watched_scan_active(domain: str) -> bool:
    with _watch_scan_lock:
        p = _watch_scan_progress.get(domain)
        return bool(p and not p.get('done'))


def _update_watch_progress(domain: str, **kw) -> None:
    with _watch_scan_lock:
        if domain in _watch_scan_progress:
            _watch_scan_progress[domain].update(kw)


def watched_threads_url(domain: str, page: int = 1) -> str:
    base = f'https://{domain}/watched/threads?unread=0'
    if page > 1:
        base += f'&page={page}'
    return base


def fetch_watched_threads_page(domain: str, page: int) -> xenforo.WatchedThreadsPage:
    """Fetch and parse one page of a site's /watched/threads listing.
    Requires a stored session cookie, same as fetch_alerts."""
    resp = _fetch(watched_threads_url(domain, page))
    watched = xenforo.parse_watched_threads(resp.text, domain)
    logged_out = '/login' in urlparse(str(resp.url)).path or \
        (page == 1 and not watched.refs and '/login/login' in resp.text)
    if logged_out:
        raise FetchBlockedError(
            f'{domain}: not logged in — paste a fresh Cookie header in'
            ' Settings → Fanfic site cookies.')
    return watched


def run_watched_scan(domain: str) -> None:
    """Walk the domain's watched-threads listing from its checkpointed page,
    queueing any (site, thread_id) not already in the library. Runs to the
    last page and then wraps back to page 1 so a later click does a full
    fresh pass (cheap: already-archived fics are an instant unique-index
    skip) and picks up anything newly watched since the last run."""
    db = get_db()
    now = int(time.time())
    row = db.execute(
        'SELECT next_page, found, imported, already_in_library'
        ' FROM fanfic_watched_scans WHERE domain=?', (domain,)).fetchone()
    if row is None:
        db.execute(
            'INSERT INTO fanfic_watched_scans(domain, next_page, updated_at)'
            ' VALUES (?, 1, ?)', (domain, now))
        db.commit()
        page, found, imported, already = 1, 0, 0, 0
    elif row['next_page'] <= 1:
        page, found, imported, already = 1, 0, 0, 0
    else:
        page = row['next_page']
        found, imported, already = row['found'], row['imported'], row['already_in_library']

    with _watch_scan_lock:
        _watch_scan_progress[domain] = {
            'page': page, 'lastPage': None, 'found': found, 'imported': imported,
            'alreadyInLibrary': already, 'done': False, 'error': None,
        }

    try:
        while True:
            with _watch_scan_lock:
                if domain not in _watch_scan_progress:
                    return
            watched = fetch_watched_threads_page(domain, page)
            for ref in watched.refs:
                found += 1
                existing = db.execute(
                    'SELECT id FROM fics WHERE site=? AND thread_id=?',
                    (ref.domain, ref.thread_id)).fetchone()
                if existing:
                    already += 1
                    continue
                fic_now = int(time.time())
                placeholder = ref.slug.replace('-', ' ').strip() or 'Importing…'
                db.execute(
                    'INSERT INTO fics(id, title, source_type, source_url, site, thread_id,'
                    ' update_pending, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)',
                    (str(ULID()), placeholder, 'xenforo', ref.thread_url, ref.domain,
                     ref.thread_id, fic_now, fic_now))
                imported += 1
            next_page = page + 1 if page < watched.last_page else 1
            db.execute(
                'UPDATE fanfic_watched_scans SET next_page=?, found=?, imported=?,'
                ' already_in_library=?, last_error=NULL, updated_at=? WHERE domain=?',
                (next_page, found, imported, already, int(time.time()), domain))
            db.commit()
            # Once the last page is done, show 'page' as the wrapped resume
            # point (1) rather than the just-finished page, so a status read
            # right after completion already matches what the DB checkpoint
            # (and a subsequent run) would show.
            shown_page = next_page if page >= watched.last_page else page
            _update_watch_progress(domain, page=shown_page, lastPage=watched.last_page,
                                    found=found, imported=imported, alreadyInLibrary=already)
            # Drain inline (this thread is already dedicated to slow, serial
            # forum requests) rather than spawning the usual background drain
            # worker — that avoids a second long-lived thread racing this
            # scan's own checkpointing, and keeps behavior identical whether
            # the caller runs this in a daemon thread or synchronously.
            run_drain_pending()
            if page >= watched.last_page:
                break
            page += 1
    except Exception as e:
        error = str(e)
        db.execute(
            'UPDATE fanfic_watched_scans SET last_error=?, updated_at=? WHERE domain=?',
            (error, int(time.time()), domain))
        db.commit()
        _update_watch_progress(domain, error=error)
    finally:
        _update_watch_progress(domain, done=True)


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


def download_images(fic_id: str, sources: list[xenforo.ImageSource]) -> dict[str, str]:
    """Download each remote image into the fic's images dir. Returns a
    url -> local-api-src mapping keyed by the source's preferred URL; when
    that fails, the forum's proxy copy is tried before giving up. Failed
    downloads are simply omitted so those images keep their remote URL."""
    mapping: dict[str, str] = {}
    img_dir = storage.images_dir(fic_id)
    if img_dir is None:
        return mapping
    img_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        url = source.url
        stem = hashlib.sha1(url.encode()).hexdigest()[:16]
        existing = next(img_dir.glob(f'{stem}.*'), None)
        if existing:
            mapping[url] = f'/api/fanfic/{fic_id}/images/{existing.name}'
            continue
        data = None
        for candidate in filter(None, (url, source.proxy_url)):
            try:
                data, content_type = _fetch_binary(candidate)
                break
            except Exception as e:
                print(f'Fanfic image download failed ({candidate}): {e}')
        if data is None:
            continue
        name = stem + _ext_for(url, content_type)
        (img_dir / name).write_bytes(data)
        mapping[url] = f'/api/fanfic/{fic_id}/images/{name}'
    return mapping


def process_post_html(fic_id: str, content_html: str, base_url: str) -> tuple[str, str, str | None]:
    """Download images, rewrite srcs, sanitize. Returns (clean_html, text,
    first local image filename or None)."""
    sources = xenforo.extract_image_sources(content_html, base_url)
    mapping = download_images(fic_id, sources)
    html = xenforo.rewrite_image_srcs(content_html, base_url, mapping)
    clean = sanitize_chapter_html(html)
    first_image = None
    for source in sources:
        if source.url in mapping:
            first_image = mapping[source.url].rsplit('/', 1)[-1]
            break
    return clean, html_to_text(clean), first_image


# --- import / update jobs ---

def _insert_chapter(db, fic_id: str, category: str, position: int,
                    post: xenforo.ReaderPost, source_url: str,
                    clean_html: str, text: str) -> str | None:
    """Insert a chapter, or return None when this post is already stored."""
    from ulid import ULID
    chapter_id = str(ULID())
    cur = db.execute(
        'INSERT OR IGNORE INTO fic_chapters'
        '(id, fic_id, position, title, category, content_html, content_text,'
        ' source_url, source_post_id, word_count, posted_at, edited_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (chapter_id, fic_id, position, post.threadmark_title or f'Chapter {position}',
         category, clean_html, text, source_url, post.post_id,
         count_words(text), post.posted_at, post.edited_at, int(time.time())),
    )
    return chapter_id if cur.rowcount > 0 else None


def _chapter_changed(stored, post: xenforo.ReaderPost) -> bool:
    """Whether the site's copy of an already-saved chapter differs from ours.

    `edited_at` is the real signal; posted_at and the threadmark title are
    checked too because a re-threadmarked or renamed post is the same kind of
    revision from the reader's point of view. Deliberately not a content
    comparison: the stored HTML has been sanitized and had its image srcs
    rewritten, so it never equals what the site just served."""
    if post.edited_at != stored['edited_at']:
        return True
    if post.posted_at is not None and post.posted_at != stored['posted_at']:
        return True
    title = post.threadmark_title
    return bool(title) and title != stored['title']


def _update_chapter(db, chapter_id: str, post: xenforo.ReaderPost,
                    clean_html: str, text: str) -> None:
    """Rewrite a saved chapter in place after the author edited it.

    `position` is untouched on purpose — reading order and the fic's
    last-read pointer must not shift because someone fixed a typo. The
    fic_chapters_fts _au trigger re-indexes content_text for free."""
    db.execute(
        "UPDATE fic_chapters SET title=COALESCE(NULLIF(?, ''), title),"
        ' content_html=?, content_text=?, word_count=?,'
        ' posted_at=COALESCE(?, posted_at), edited_at=? WHERE id=?',
        (post.threadmark_title or '', clean_html, text, count_words(text),
         post.posted_at, post.edited_at, chapter_id),
    )


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


def _sync_site_tags(db, fic_id: str, ref: xenforo.ThreadRef) -> None:
    """Fetch the main thread page and replace the fic's site tags.
    Best-effort: a fetch/parse failure, or an empty result (usually a login
    wall rather than an untagged thread), leaves existing tags alone."""
    try:
        tags = xenforo.parse_thread_tags(_fetch(ref.thread_url).text)
    except Exception as e:
        print(f'Fanfic tag fetch failed for {fic_id}: {e}')
        return
    if not tags:
        return
    now = int(time.time())
    db.execute('DELETE FROM fic_site_tags WHERE fic_id=?', (fic_id,))
    db.executemany(
        'INSERT OR IGNORE INTO fic_site_tags(fic_id, name, created_at) VALUES (?,?,?)',
        [(fic_id, t, now) for t in tags])
    db.commit()


def _fail_fic(fic_id: str, error: str) -> None:
    db = get_db()
    db.execute(
        'UPDATE fics SET download_status=?, download_error=?, updated_at=? WHERE id=?',
        ('error', error, int(time.time()), fic_id),
    )
    db.commit()
    _update_progress(fic_id, phase='error', error=error, done=True)


class WalkResult(NamedTuple):
    inserted: int
    updated: int
    saw_posts: bool  # the reader view actually served us posts
    first_author: str | None


def _stored_entry(post: xenforo.ReaderPost, chapter_id: str | None) -> dict:
    """The subset of a chapter row `_chapter_changed` compares against."""
    return {'id': chapter_id, 'title': post.threadmark_title,
            'posted_at': post.posted_at, 'edited_at': post.edited_at}


def _walk_category(db, fic_id: str, ref: xenforo.ThreadRef,
                   cat: xenforo.ThreadmarkCategory, start_position: int,
                   start_page: int = 1, stored: dict[str, dict] | None = None,
                   update_existing: bool = False) -> WalkResult:
    """Download one threadmark category's chapters. Tries the reader view
    (~10 chapters per request); when the reader is unavailable (QQ forbids
    it) falls back to harvesting posts from the thread pages themselves.

    `stored` maps an already-saved post id to its comparable fields; with
    `update_existing` those posts are re-checked for edits instead of being
    skipped outright."""
    stored = stored if stored is not None else {}
    result = _walk_category_reader(
        db, fic_id, ref, cat, start_position, start_page, stored, update_existing)
    # Fall back only when the reader gave us nothing at all — a walk that saw
    # posts but changed none is the normal up-to-date outcome, and retrying it
    # through the thread pages would re-fetch the whole threadmarks index for
    # no reason on every check.
    if not result.saw_posts and not _cancelled(fic_id):
        result = _walk_category_via_thread(
            db, fic_id, ref, cat, start_position, stored, update_existing)
    return result


def _walk_category_reader(db, fic_id: str, ref: xenforo.ThreadRef,
                          cat: xenforo.ThreadmarkCategory, start_position: int,
                          start_page: int, stored: dict[str, dict],
                          update_existing: bool) -> WalkResult:
    """Page through one threadmark category's reader, inserting new chapters
    and rewriting edited ones. Committed per reader page."""
    position = start_position
    inserted = updated = 0
    saw_posts = False
    first_author: str | None = None
    page = start_page
    while True:
        if _cancelled(fic_id):
            return WalkResult(inserted, updated, saw_posts, first_author)
        try:
            resp = _fetch(ref.reader_url(cat.category_id, page))
        except FetchBlockedError:
            raise
        except Exception:
            # Reader unavailable (QQ 403s it, or an empty category 404s):
            # report that we saw nothing so the caller falls back to the
            # thread-page walk.
            if page == start_page:
                return WalkResult(inserted, updated, saw_posts, first_author)
            raise
        reader = xenforo.parse_reader_page(resp.text)
        if not reader.posts:
            return WalkResult(inserted, updated, saw_posts, first_author)
        saw_posts = True
        if first_author is None:
            first_author = reader.posts[0].author
        for post in reader.posts:
            existing = stored.get(post.post_id)
            if existing is not None:
                if not update_existing or not _chapter_changed(existing, post):
                    continue
                clean, text, _ = process_post_html(fic_id, post.content_html, str(resp.url))
                _update_chapter(db, existing['id'], post, clean, text)
                stored[post.post_id] = _stored_entry(post, existing['id'])
                updated += 1
                continue
            clean, text, _ = process_post_html(fic_id, post.content_html, str(resp.url))
            position += 1
            source_url = f'{ref.thread_url}post-{post.post_id}'
            chapter_id = _insert_chapter(
                db, fic_id, cat.name, position, post, source_url, clean, text)
            if chapter_id:
                inserted += 1
                stored[post.post_id] = _stored_entry(post, chapter_id)
            else:
                position -= 1
        db.commit()
        _bump_progress(fic_id, len(reader.posts))
        if page >= reader.last_page:
            return WalkResult(inserted, updated, saw_posts, first_author)
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
                              stored: dict[str, dict],
                              update_existing: bool) -> WalkResult:
    """Reader-less fallback: list the category's chapters from the
    threadmarks index, then walk the thread pages that contain them. Each
    post URL redirects to its thread page, whose parsed posts are cached so
    every page is fetched once.

    Without `update_existing` only missing chapters are visited. With it every
    chapter is — the only way to see an edit on a site that forbids /reader,
    and the reason this depth runs on a cadence rather than every check."""
    items = _collect_threadmark_items(fic_id, ref, cat)
    if not update_existing:
        items = [i for i in items if i.post_id not in stored]
    if not items:
        return WalkResult(0, 0, False, None)

    harvested: dict[str, xenforo.ReaderPost] = {}
    harvested_meta: dict[str, str] = {}  # post_id -> base_url of its page
    position = start_position
    inserted = updated = 0
    saw_posts = False
    first_author: str | None = None
    wanted = {i.post_id for i in items}

    for item in items:
        if _cancelled(fic_id):
            return WalkResult(inserted, updated, saw_posts, first_author)
        if item.post_id not in harvested:
            resp = _fetch(ref.post_url(item.post_id))
            page = xenforo.parse_reader_page(resp.text)
            for post in page.posts:
                if post.post_id in wanted and post.post_id not in harvested:
                    harvested[post.post_id] = post
                    harvested_meta[post.post_id] = str(resp.url)
        post = harvested.get(item.post_id)
        if post is None:
            print(f'Fanfic thread-walk: post {item.post_id} not found on its page, skipping')
            continue
        saw_posts = True
        if first_author is None:
            first_author = post.author
        if not post.threadmark_title:
            post.threadmark_title = item.title
        if post.posted_at is None:
            post.posted_at = item.posted_at

        existing = stored.get(item.post_id)
        if existing is not None:
            if not update_existing or not _chapter_changed(existing, post):
                continue
            clean, text, _ = process_post_html(
                fic_id, post.content_html, harvested_meta[item.post_id])
            _update_chapter(db, existing['id'], post, clean, text)
            stored[item.post_id] = _stored_entry(post, existing['id'])
            updated += 1
            db.commit()
            continue

        clean, text, _ = process_post_html(fic_id, post.content_html, harvested_meta[item.post_id])
        position += 1
        source_url = f'{ref.thread_url}post-{post.post_id}'
        chapter_id = _insert_chapter(
            db, fic_id, cat.name, position, post, source_url, clean, text)
        if chapter_id:
            inserted += 1
            stored[post.post_id] = _stored_entry(post, chapter_id)
            db.commit()
            _bump_progress(fic_id, 1)
        else:
            position -= 1
    return WalkResult(inserted, updated, saw_posts, first_author)


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

        _sync_site_tags(db, fic_id, ref)

        counts = [c.count for c in index.categories]
        total = sum(counts) if all(c is not None for c in counts) else None
        _update_progress(fic_id, phase='chapters', chaptersTotal=total)

        imported = 0
        author = index.author
        for cat in index.categories:
            result = _walk_category(db, fic_id, ref, cat, start_position=0)
            imported += result.inserted
            # The threadmarks index rarely names the author; the first
            # threadmarked post's author is the fic author in practice.
            if author is None and result.first_author:
                author = result.first_author
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


def _stored_chapters(db, fic_id: str, category: str) -> dict[str, dict]:
    """Already-saved chapters of one category, keyed by post id."""
    rows = db.execute(
        'SELECT id, source_post_id, title, posted_at, edited_at'
        ' FROM fic_chapters WHERE fic_id=? AND category=?',
        (fic_id, category)).fetchall()
    return {r['source_post_id']: dict(r) for r in rows if r['source_post_id']}


def _first_missing_page(fic_id: str, ref: xenforo.ThreadRef,
                        cat: xenforo.ThreadmarkCategory,
                        stored: dict[str, dict]) -> int | None:
    """Reader page holding the earliest chapter we're missing, or None when
    the category's threadmark list is fully accounted for.

    This replaces deriving a start page from our *row count*, which overshoots
    whenever there's a gap in what we stored — a failed import or a post that
    403'd left later checks skipping the very pages holding the chapters we
    never got. The reader paginates the same ordered threadmark list the index
    does, so an index position maps exactly onto a reader page."""
    try:
        items = _collect_threadmark_items(fic_id, ref, cat)
    except FetchBlockedError:
        raise
    except Exception as e:
        # Not every theme serves a per-category threadmarks page. Walking from
        # the start costs requests but can't miss a chapter; guessing a start
        # page from our row count is exactly the bug this replaces.
        print(f'Fanfic update: threadmark index unavailable for {fic_id}/{cat.name}: {e}')
        return 1
    if not items:
        # Index unreadable or the walk was cancelled — walk from the start
        # rather than concluding there's nothing to fetch.
        return 1
    for i, item in enumerate(items):
        if item.post_id not in stored:
            return i // POSTS_PER_READER_PAGE + 1
    return None


def run_check_updates(fic_id: str, deep: bool = False) -> None:
    """Check one fic against the site.

    A cheap check looks for chapters we don't have. A deep check re-reads
    every saved chapter and rewrites the ones the author edited — XenForo
    raises no alert for an edit and leaves the threadmarks index untouched,
    so nothing cheaper can see one.

    Deep only ever runs when explicitly asked for. Authors revising published
    chapters is rare enough that paying a full re-walk on a timer would cost
    far more requests than it recovers."""
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

        _sync_site_tags(db, fic_id, ref)

        author = index.author
        for cat in index.categories:
            maxpos = db.execute(
                'SELECT COALESCE(MAX(position), 0) AS maxpos'
                ' FROM fic_chapters WHERE fic_id=? AND category=?',
                (fic_id, cat.name)).fetchone()['maxpos']
            stored = _stored_chapters(db, fic_id, cat.name)

            if deep:
                start_page = 1
            else:
                # The site's "Statistics (N threadmarks)" is deliberately NOT
                # used to skip a category. It counts a different population
                # than our rows do — threadmarks get recategorised, renamed and
                # deleted on long threads — so the two drift apart, and any
                # count-based shortcut then either latches the fic shut
                # (stored > site) or silently agrees while the sets differ
                # (equal counts, swapped members). Diffing post ids is the only
                # comparison that can't be fooled; it costs one index fetch per
                # ~50 threadmarks, and returns None when nothing is missing.
                start_page = _first_missing_page(fic_id, ref, cat, stored)
                if start_page is None:
                    continue

            result = _walk_category(
                db, fic_id, ref, cat, start_position=maxpos,
                start_page=start_page, stored=stored, update_existing=deep)
            if author is None and result.first_author:
                author = result.first_author
            if _cancelled(fic_id):
                return

        total = db.execute('SELECT COUNT(*) AS n FROM fic_chapters WHERE fic_id=?',
                           (fic_id,)).fetchone()['n']
        if total == 0:
            _fail_fic(fic_id, 'No threadmarked chapters found — does this thread have threadmarks?')
            return
        if author and not index.author:
            db.execute('UPDATE fics SET author=COALESCE(author, ?) WHERE id=?', (author, fic_id))

        try:
            _repair_remote_images(db, fic_id, ref)
        except Exception as e:
            print(f'Fanfic image repair failed for {fic_id}: {e}')

        _finalize_fic(db, fic_id, _first_local_image(db, fic_id))
        _update_progress(fic_id, phase='done', done=True)
    except Exception as e:
        print(f'Fanfic update check failed for {fic_id}: {e}')
        _fail_fic(fic_id, str(e))


# --- pending-update queue ---
#
# Bulk updates (alerts refresh, the per-fic Update button) never fetch
# directly: they set fics.update_pending and a single drain worker walks the
# flags one fic at a time. Strictly serial on purpose — parallel scrapes get
# the account rate-limited or Cloudflare-banned. The flag persists across
# restarts; a leftover queue resumes on the next drain trigger.

_drain_lock = threading.Lock()
_drain_active = False


def start_drain() -> None:
    """Spawn the drain worker unless one is already running. A running
    worker re-queries the flags each round, so newly flagged fics are picked
    up without a second thread."""
    global _drain_active
    with _drain_lock:
        if _drain_active:
            return
        _drain_active = True

    def worker():
        global _drain_active
        try:
            run_drain_pending()
        finally:
            with _drain_lock:
                _drain_active = False

    threading.Thread(target=worker, daemon=True).start()


def run_drain_pending() -> None:
    db = get_db()
    while True:
        row = db.execute(
            "SELECT id, deep_pending FROM fics WHERE update_pending=1"
            " AND download_status != 'downloading'"
            ' ORDER BY updated_at LIMIT 1').fetchone()
        if not row:
            return
        fic_id = row['id']
        deep = bool(row['deep_pending'])
        db.execute(
            'UPDATE fics SET update_pending=0, deep_pending=0,'
            " download_status='downloading' WHERE id=?",
            (fic_id,))
        db.commit()
        start_progress(fic_id, 'updating')
        try:
            run_check_updates(fic_id, deep=deep)
        except Exception as e:
            # run_check_updates handles its own errors; this guards the queue
            # against anything that escapes so one bad fic can't stop the rest.
            print(f'Fanfic drain: update crashed for {fic_id}: {e}')
            _fail_fic(fic_id, str(e))


_REMOTE_IMG = re.compile(r'<img[^>]+src="https?://', re.I)


def _repair_remote_images(db, fic_id: str, ref: xenforo.ThreadRef) -> int:
    """Chapters whose images couldn't be downloaded keep remote srcs; many
    are recoverable later (e.g. via the forum's proxy cache, or once a site
    cookie is stored). Re-fetch those chapters' thread pages and re-process
    the ones that improve. Returns the number of chapters repaired."""
    rows = db.execute(
        "SELECT id, source_post_id, content_html FROM fic_chapters"
        " WHERE fic_id=? AND source_post_id IS NOT NULL AND content_html LIKE '%<img%'",
        (fic_id,)).fetchall()
    broken = [r for r in rows if _REMOTE_IMG.search(r['content_html'])]
    if not broken:
        return 0

    # One post URL fetch resolves a whole thread page — cache its posts so
    # several broken chapters on the same page cost a single request.
    harvested: dict[str, xenforo.ReaderPost] = {}
    harvested_meta: dict[str, str] = {}
    repaired = 0
    for row in broken:
        if _cancelled(fic_id):
            break
        post_id = row['source_post_id']
        if post_id not in harvested:
            try:
                resp = _fetch(ref.post_url(post_id))
            except Exception as e:
                print(f'Fanfic image repair: fetch failed for post {post_id}: {e}')
                continue
            for post in xenforo.parse_reader_page(resp.text).posts:
                if post.post_id not in harvested:
                    harvested[post.post_id] = post
                    harvested_meta[post.post_id] = str(resp.url)
        post = harvested.get(post_id)
        if post is None:
            continue
        clean, text, _ = process_post_html(fic_id, post.content_html, harvested_meta[post_id])
        if len(_REMOTE_IMG.findall(clean)) < len(_REMOTE_IMG.findall(row['content_html'])):
            db.execute(
                'UPDATE fic_chapters SET content_html=?, content_text=?, word_count=? WHERE id=?',
                (clean, text, count_words(text), row['id']))
            db.commit()
            repaired += 1
    return repaired
