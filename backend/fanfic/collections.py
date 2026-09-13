"""Checkpointed collection discovery feeding the existing serial download queue."""

import json
import threading
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.fanfic import download, sites
from backend.fanfic.sanitize import sanitize_chapter_html, html_to_text
from backend.fanfic.xenforo import ReaderPost

_lock = threading.Lock()
_running = False


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
    db.commit()
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
            db.execute("UPDATE fanfic_collection_scans SET status='pending',error=NULL WHERE id=?",
                       (row['id'],))
            db.commit()
            return row['id']
        scan_id = row['id'] if row else str(ULID())
        db.execute('INSERT INTO fanfic_collection_scans'
                   '(id,site,collection,username,remaining_urls,status,updated_at)'
                   " VALUES (?,?,?,?,?,'pending',?) ON CONFLICT(id) DO UPDATE SET"
                   " remaining_urls=excluded.remaining_urls,status='pending',error=NULL,"
                   'found=0,imported=0,skipped=0,pages=0,updated_at=excluded.updated_at',
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
            db.execute('UPDATE fanfic_collection_scans SET remaining_urls=?,found=?,imported=?,skipped=?,'
                       'pages=?,updated_at=? WHERE id=?',
                       (json.dumps(urls), found, imported, skipped, pages, int(time.time()), scan_id))
            db.commit()
        db.execute("UPDATE fanfic_collection_scans SET status='complete',error=NULL WHERE id=?", (scan_id,))
        db.commit()
    except Exception as exc:
        db.execute("UPDATE fanfic_collection_scans SET status='error',error=?,updated_at=? WHERE id=?",
                   (str(exc), int(time.time()), scan_id))
        db.commit()


def start_scans() -> None:
    global _running
    with _lock:
        if _running:
            return
        _running = True

    def worker():
        global _running
        try:
            while True:
                with _lock:
                    row = get_db().execute("SELECT id FROM fanfic_collection_scans WHERE status='pending'"
                                           ' ORDER BY updated_at LIMIT 1').fetchone()
                    if row is None:
                        break
                run_scan(row['id'])
        finally:
            with _lock:
                _running = False
                pending = get_db().execute("SELECT 1 FROM fanfic_collection_scans WHERE status='pending' LIMIT 1").fetchone()
            if pending:
                start_scans()
            download.start_drain()

    threading.Thread(target=worker, daemon=True).start()
