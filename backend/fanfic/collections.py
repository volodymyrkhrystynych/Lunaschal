"""Checkpointed collection discovery feeding the existing serial download queue."""

import json
import threading
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.fanfic import download, personal_tags, sites, pacing
from backend.fanfic.sanitize import sanitize_chapter_html, html_to_text
from backend.fanfic.xenforo import ReaderPost

_lock = threading.Lock()
_running = False
# Set to cut a deferral sleep short when a scan becomes runnable — a
# user asking for one must not wait out somebody else's backoff.
_wake = threading.Event()

# How long to wait before re-running a scan whose page fetch failed for a
# reason that is nobody's decision — a timeout, a dropped connection, a CDN
# answering 525 for a URL that works seconds later. One entry per attempt;
# running out of entries is what turns a deferral into a real error, so a
# site that is genuinely gone stops being retried instead of looping against
# it forever. The page position is already checkpointed in remaining_urls,
# so each retry resumes rather than restarting.
RETRY_SCHEDULE = (60, 300, 900)


def _fetch(url):
    return download._fetch(url, same_host=True)


def queue_work(ref: sites.WorkRef) -> tuple[str, bool]:
    db = get_db()
    now, fic_id = int(time.time()), str(ULID())
    result = db.execute(
        'INSERT OR IGNORE INTO fics(id,title,source_type,source_url,site,thread_id,'
        'update_pending,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)',
        (fic_id, f'Importing {ref.source_type} {ref.id}…', ref.source_type,
         ref.url, ref.site, ref.id, now, now))
    created = result.rowcount > 0
    if not created:
        row = db.execute('SELECT id,download_status FROM fics WHERE site=? AND thread_id=?',
                         (ref.site, ref.id)).fetchone()
        fic_id = row['id']
        if row['download_status'] == 'error':
            db.execute('UPDATE fics SET update_pending=1 WHERE id=?', (fic_id,))
    # Scanning an existing story enriches its history without downloading it
    # again. Missing dates on later scans never erase a previously saved date.
    db.execute('UPDATE fics SET source_favorited_at=COALESCE(?,source_favorited_at),'
               'source_followed_at=COALESCE(?,source_followed_at) WHERE id=?',
               (ref.favorited_at, ref.followed_at, fic_id))
    db.commit()
    # A rescan of the bookmarks is how folders stay current, so this runs for
    # an already-present story too and not only for a fresh import.
    personal_tags.sync_personal_folders(db, fic_id, ref.tags)
    return fic_id, created


def run_work(fic_id: str, url: str, deep: bool = False) -> None:
    db = get_db()
    try:
        ref = sites.parse_work_url(url)
        if ref is None:
            raise ValueError('Unsupported work URL')
        if ref.source_type == 'ao3':
            book = sites.parse_ao3(_fetch(ref.url + '?view_full_work=true&view_adult=true').text, ref)
        elif ref.source_type == 'fanfiction':
            book = sites.parse_ffn(_fetch(ref.url).text, ref)
        else:
            book = sites.parse_patreon(_fetch(sites.patreon_api('posts/' + ref.id)).json())
        db.execute('UPDATE fics SET title=?,author=?,description=? WHERE id=?',
                   (book['title'], book['author'], book['description'], fic_id))
        db.commit()
        download._update_progress(fic_id, phase='chapters', chaptersTotal=book['total'])
        existing = {r['source_post_id']: r['id'] for r in db.execute(
            'SELECT id,source_post_id FROM fic_chapters WHERE fic_id=?', (fic_id,))}
        # The first FF.net page includes the whole chapter selector. Refresh
        # titles even when existing chapter bodies do not need downloading.
        if ref.source_type == 'fanfiction':
            for position, title in book['chapter_titles'].items():
                if title and str(position) in existing:
                    db.execute('UPDATE fic_chapters SET title=? WHERE id=?',
                               (title, existing[str(position)]))
            db.commit()
        for position in range(1, book['total'] + 1):
            if download._cancelled(fic_id):
                return
            if ref.source_type == 'fanfiction':
                if str(position) in existing and not deep:
                    download._bump_progress(fic_id, 1)
                    continue
                page = book if position == 1 else sites.parse_ffn(
                    _fetch(f'https://www.fanfiction.net/s/{ref.id}/{position}/').text, ref, position)
                chapter = page['chapters'][0]
                chapter_url = f'https://www.fanfiction.net/s/{ref.id}/{position}/'
            else:
                chapter = book['chapters'][position - 1]
                chapter_url = ref.url
            key, title, html, posted = chapter
            # AO3 one-shots don't render chapter permalinks. When the author
            # adds another chapter, promote the fallback identity in place.
            fallback = f'position-{position}'
            if ref.source_type == 'ao3' and key not in existing and fallback in existing:
                chapter_id = existing.pop(fallback)
                db.execute('UPDATE fic_chapters SET source_post_id=? WHERE id=?', (key, chapter_id))
                db.commit()
                existing[key] = chapter_id
            if key not in existing or deep:
                clean = sanitize_chapter_html(html)
                text = html_to_text(clean)
                if not text:
                    raise ValueError(f'Chapter {position} has no readable text')
                post = ReaderPost(key, title, book['author'], posted, clean)
                if key in existing:
                    download._update_chapter(db, existing[key], post, clean, text)
                else:
                    download._insert_chapter(db, fic_id, 'chapters', position, post, chapter_url, clean, text)
                db.execute('UPDATE fics SET chapter_count=(SELECT COUNT(*) FROM fic_chapters WHERE fic_id=?),'
                           'word_count=(SELECT COALESCE(SUM(word_count),0) FROM fic_chapters WHERE fic_id=?)'
                           ' WHERE id=?', (fic_id, fic_id, fic_id))
                db.commit()
            download._bump_progress(fic_id, 1)
        download._finalize_fic(db, fic_id, None)
        download._update_progress(fic_id, phase='done', done=True)
    except pacing.DeferredDownload as exc:
        db.execute("UPDATE fics SET download_status='error',update_pending=1,deep_pending=?,"
                   'download_error=? WHERE id=?', (int(deep), str(exc), fic_id))
        db.commit()
        download._update_progress(fic_id, phase='paused', error=str(exc), done=True)
    except Exception as exc:
        download._fail_fic(fic_id, str(exc))


def create_scan(site: str, collection: str, username: str) -> str:
    urls = sites.collection_urls(site, collection, username)
    db = get_db()
    # Repeat clicks and retries continue the same checkpoint; completed scans
    # begin again to discover additions while queue_work deduplicates stories.
    with _lock:
        row = db.execute('SELECT id,status FROM fanfic_collection_scans'
                         ' WHERE site=? AND collection=? AND username=?',
                         (site, collection, username)).fetchone()
        if row and row['status'] != 'complete':
            # Asking again is asking for now: drop any deferral so the worker
            # picks the scan up on this pass rather than at its backoff.
            db.execute("UPDATE fanfic_collection_scans SET status='pending',error=NULL,"
                       'attempts=0,retry_after=0 WHERE id=?',
                       (row['id'],))
            db.commit()
            return row['id']
        scan_id = row['id'] if row else str(ULID())
        db.execute('INSERT INTO fanfic_collection_scans'
                   '(id,site,collection,username,remaining_urls,status,updated_at)'
                   " VALUES (?,?,?,?,?,'pending',?) ON CONFLICT(id) DO UPDATE SET"
                   " remaining_urls=excluded.remaining_urls,status='pending',error=NULL,"
                   'found=0,imported=0,skipped=0,pages=0,attempts=0,retry_after=0,'
                   'updated_at=excluded.updated_at',
                   (scan_id, site, collection, username, json.dumps(urls), int(time.time())))
        db.commit()
        return scan_id


def run_scan(scan_id: str) -> None:
    db = get_db()
    row = db.execute('SELECT * FROM fanfic_collection_scans WHERE id=?', (scan_id,)).fetchone()
    urls = json.loads(row['remaining_urls'])
    found, imported, skipped, pages = (row[k] for k in ('found', 'imported', 'skipped', 'pages'))
    visited = set()
    try:
        while urls:
            url = urls[0]
            if url in visited:
                raise ValueError('The site repeated a collection page; scan stopped to avoid a loop')
            visited.add(url)
            response = _fetch(url)
            if any(part in str(response.url) for part in ('/login', '/users/login')):
                raise ValueError('Session expired; update your site cookies in Settings')
            if row['site'] == 'patreon.com':
                refs, next_url, inaccessible = sites.parse_patreon_collection(response.json(), url)
                skipped += inaccessible
            else:
                refs, next_url = sites.parse_collection(response.text, url)
            for ref in refs:
                _, created = queue_work(ref)
                found += 1
                imported += int(created)
            urls = ([next_url] if next_url else []) + urls[1:]
            pages += 1
            # A page that came back resets the deferral counter: a long walk
            # that meets one flaky page every few hundred must not accumulate
            # its way to a permanent error.
            db.execute('UPDATE fanfic_collection_scans SET remaining_urls=?,found=?,imported=?,skipped=?,'
                       'pages=?,attempts=0,retry_after=0,updated_at=? WHERE id=?',
                       (json.dumps(urls), found, imported, skipped, pages, int(time.time()), scan_id))
            db.commit()
        db.execute("UPDATE fanfic_collection_scans SET status='complete',error=NULL WHERE id=?", (scan_id,))
        db.commit()
    except pacing.DeferredDownload as exc:
        db.execute("UPDATE fanfic_collection_scans SET status='pending',error=? WHERE id=?",
                   (str(exc), scan_id))
        db.commit()
    except Exception as exc:
        attempts = (row['attempts'] or 0) + 1
        if download.is_transient(exc) and attempts <= len(RETRY_SCHEDULE):
            # Stay pending so the worker resumes from remaining_urls, but not
            # before retry_after — re-running immediately is a busy loop
            # against a site that just failed to answer.
            db.execute("UPDATE fanfic_collection_scans SET status='pending',error=?,attempts=?,"
                       'retry_after=?,updated_at=? WHERE id=?',
                       (str(exc), attempts, int(time.time()) + RETRY_SCHEDULE[attempts - 1],
                        int(time.time()), scan_id))
        else:
            db.execute("UPDATE fanfic_collection_scans SET status='error',error=?,attempts=?,"
                       'retry_after=0,updated_at=? WHERE id=?',
                       (str(exc), attempts, int(time.time()), scan_id))
        db.commit()


# A scan the worker may pick up right now: pending, its site not paused or
# cooling down, and past any deferral from a failed page fetch.
_RUNNABLE_SQL = ("SELECT id FROM fanfic_collection_scans WHERE status='pending'"
                 ' AND retry_after<=unixepoch()'
                 ' AND NOT EXISTS (SELECT 1 FROM fanfic_site_limits l'
                 ' WHERE l.domain=fanfic_collection_scans.site'
                 ' AND (l.paused=1 OR l.cooldown_until>unixepoch()))')


def _next_retry_wait() -> float | None:
    """Seconds until the soonest deferred scan is runnable, or None if there
    is no deferred scan to wait for."""
    row = get_db().execute("SELECT MIN(retry_after) AS due FROM fanfic_collection_scans"
                           " WHERE status='pending' AND retry_after>unixepoch()").fetchone()
    return max(0.0, row['due'] - time.time()) if row and row['due'] else None


def start_scans() -> None:
    global _running
    with _lock:
        if _running:
            # A worker already exists but may be sleeping out a deferral.
            _wake.set()
            return
        _running = True

    def worker():
        global _running
        _wake.clear()
        try:
            while True:
                with _lock:
                    row = get_db().execute(_RUNNABLE_SQL + ' ORDER BY updated_at LIMIT 1').fetchone()
                    wait = None if row is not None else _next_retry_wait()
                    if row is None and wait is None:
                        break
                if row is None:
                    # Nothing to run yet, but a deferred scan is due later.
                    # Hold the thread rather than exiting: whoever deferred
                    # the scan is the only one who knows it needs waking, and
                    # nothing else polls this table. Capped at a minute so a
                    # cooldown that lifts elsewhere isn't waited out in full.
                    _wake.wait(min(wait, 60))
                    _wake.clear()
                    continue
                run_scan(row['id'])
        finally:
            with _lock:
                _running = False
                pending = get_db().execute(_RUNNABLE_SQL + ' LIMIT 1').fetchone()
            if pending:
                start_scans()
            download.start_drain()

    threading.Thread(target=worker, daemon=True).start()
