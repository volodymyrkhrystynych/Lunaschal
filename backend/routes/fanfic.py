import sqlite3
import threading
import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.fanfic import download, storage, xenforo
from backend.fanfic.download import FetchBlockedError
from backend.fanfic.xenforo import KNOWN_SITES, UnsupportedUrlError

bp = Blueprint('fanfic', __name__, url_prefix='/api/fanfic')

_LIST_COLS = (
    'id, title, author, source_type, source_url, site, cover_path, word_count,'
    ' chapter_count, download_status, download_error, update_pending, deep_pending,'
    ' last_read_chapter_id, last_checked_at, last_opened_at, rating, review,'
    ' description, created_at, updated_at'
)

_CHAPTER_LIST_COLS = (
    'c.id, c.fic_id, c.position, c.title, c.category, c.word_count, c.posted_at,'
    ' c.created_at, c.updated_at'
)

# Newest forum activity first: latest threadmark's forum post date, falling
# back to import time for chapters without one (epub/docx uploads), then to
# the fic's own creation for fics with no chapters yet.
_LATEST_ACTIVITY_ORDER = (
    'COALESCE('
    ' (SELECT MAX(posted_at) FROM fic_chapters WHERE fic_chapters.fic_id = fics.id),'
    ' (SELECT MAX(created_at) FROM fic_chapters WHERE fic_chapters.fic_id = fics.id),'
    ' fics.created_at'
    ') DESC'
)

_UPDATED_ORDER = 'fics.updated_at DESC, fics.created_at DESC'
_OPENED_ORDER = 'fics.last_opened_at DESC NULLS LAST, fics.updated_at DESC'


def _attach_progress(dicts: list[dict]) -> list[dict]:
    for d in dicts:
        progress = download.get_progress(d['id'])
        if progress:
            d['downloadProgress'] = progress
        if 'updatePending' in d:
            d['updatePending'] = bool(d['updatePending'])
        if 'deepPending' in d:
            d['deepPending'] = bool(d['deepPending'])
    return dicts


def _attach_library_meta(dicts: list[dict]) -> list[dict]:
    """Batch-attach folderIds, site tags and read-chapter counts."""
    if not dicts:
        return dicts
    db = get_db()
    ids = [d['id'] for d in dicts]
    placeholders = ','.join('?' * len(ids))
    folders: dict[str, list[str]] = {}
    tags: dict[str, list[str]] = {}
    reads: dict[str, int] = {}
    for r in db.execute(
            f'SELECT fic_id, folder_id FROM fic_folder_items WHERE fic_id IN ({placeholders})'
            ' ORDER BY created_at, rowid', ids):
        folders.setdefault(r['fic_id'], []).append(r['folder_id'])
    for r in db.execute(
            f'SELECT fic_id, name FROM fic_site_tags WHERE fic_id IN ({placeholders})'
            ' ORDER BY created_at, rowid', ids):
        tags.setdefault(r['fic_id'], []).append(r['name'])
    for r in db.execute(
            f'SELECT fic_id, COUNT(*) AS n FROM fic_chapter_reads'
            f' WHERE fic_id IN ({placeholders}) GROUP BY fic_id', ids):
        reads[r['fic_id']] = r['n']
    for d in dicts:
        d['folderIds'] = folders.get(d['id'], [])
        d['tags'] = tags.get(d['id'], [])
        d['readCount'] = reads.get(d['id'], 0)
    return dicts


@bp.get('')
def list_fics():
    limit = min(int(request.args.get('limit', 100)), 200)
    offset = int(request.args.get('offset', 0))
    where = []
    params: list = []
    folder_id = request.args.get('folderId')
    if folder_id == 'unsorted':
        where.append('NOT EXISTS (SELECT 1 FROM fic_folder_items WHERE fic_id=fics.id)')
    elif folder_id:
        where.append('EXISTS (SELECT 1 FROM fic_folder_items'
                     ' WHERE folder_id=? AND fic_id=fics.id)')
        params.append(folder_id)
    tag = request.args.get('tag')
    if tag:
        where.append('EXISTS (SELECT 1 FROM fic_site_tags'
                     ' WHERE name=? AND fic_id=fics.id)')
        params.append(tag)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ''
    # All and folder views follow metadata/content updates. Recent is reading
    # history, deliberately independent of forum publication dates.
    sort = request.args.get('sort')
    if sort == 'recent':
        order = _OPENED_ORDER
    elif folder_id:
        # Preserve the existing ordering inside Unsorted and named folders.
        order = _LATEST_ACTIVITY_ORDER
    else:
        order = _UPDATED_ORDER
    rows = get_db().execute(
        f'SELECT {_LIST_COLS} FROM fics{where_sql}'
        f' ORDER BY {order} LIMIT ? OFFSET ?',
        (*params, limit, offset),
    ).fetchall()
    return jsonify(_attach_library_meta(_attach_progress([row_to_dict(r) for r in rows])))


def _like_pattern(word: str) -> str:
    escaped = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


@bp.get('/search')
def search():
    """Match fics by title or site tag only — every word of the query must
    appear as a substring of the title or of one of the fic's tags."""
    query = request.args.get('query', '').strip()
    words = query.split()
    if not words:
        return jsonify([])
    clause = ("(title LIKE ? ESCAPE '\\' OR EXISTS"
              " (SELECT 1 FROM fic_site_tags"
              "  WHERE fic_id = fics.id AND name LIKE ? ESCAPE '\\'))")
    where_sql = ' AND '.join(clause for _ in words)
    params = [p for w in words for p in (_like_pattern(w), _like_pattern(w))]
    rows = get_db().execute(
        f'SELECT {_LIST_COLS} FROM fics WHERE {where_sql}'
        f' ORDER BY {_LATEST_ACTIVITY_ORDER} LIMIT 100',
        params,
    ).fetchall()
    return jsonify(_attach_library_meta(_attach_progress([row_to_dict(r) for r in rows])))


@bp.get('/cookies')
def list_cookies():
    db = get_db()
    rows = db.execute('SELECT domain, updated_at, user_agent FROM site_cookies').fetchall()
    stored = {r['domain']: r for r in rows}
    scan_rows = {
        r['domain']: r for r in db.execute(
            'SELECT domain, next_page, found, imported, already_in_library, last_error'
            ' FROM fanfic_watched_scans').fetchall()
    }
    result = []
    for domain in sorted(KNOWN_SITES):
        entry = {
            'domain': domain,
            'hasCookie': domain in stored,
            'updatedAt': row_to_dict(stored[domain])['updatedAt'] if domain in stored else None,
            'hasUserAgent': bool(stored[domain]['user_agent']) if domain in stored else False,
        }
        progress = download.get_watched_scan_progress(domain)
        if progress:
            entry['watchedScan'] = {
                'page': progress['page'], 'lastPage': progress['lastPage'],
                'found': progress['found'], 'imported': progress['imported'],
                'alreadyInLibrary': progress['alreadyInLibrary'],
                'done': progress['done'], 'error': progress['error'],
            }
        elif domain in scan_rows:
            r = scan_rows[domain]
            entry['watchedScan'] = {
                'page': r['next_page'], 'lastPage': None,
                'found': r['found'], 'imported': r['imported'],
                'alreadyInLibrary': r['already_in_library'],
                'done': True, 'error': r['last_error'],
            }
        result.append(entry)
    return jsonify(result)


class CookieInputError(ValueError):
    pass


def _reject_non_ascii(cookie: str) -> str:
    """Cookie values are restricted to a visible-ASCII subset by RFC 6265, so
    any non-ASCII character means the copy-paste got mangled before it
    reached us — most often a long value (cf_clearance routinely runs past
    what a devtools panel renders) that got truncated to a '…' by whatever
    copied it. Silently dropping that character used to be tried, but it
    just splices the two surviving fragments into a token that never existed
    and fails in a *different*, more confusing place (a rejected login)
    instead of here. Reject loudly instead, so the fix is a re-copy, not a
    guess."""
    bad = [c for c in cookie if ord(c) > 127]
    if not bad:
        return cookie
    if '…' in bad:
        raise CookieInputError(
            "Cookie contains a '…' truncation artifact — the copy method cut off a long "
            'value (commonly cf_clearance). In Firefox, use the Storage/Cookies panel or '
            "right-click the request → Copy Value on the raw Cookie header, not a "
            'display that elides long text.')
    raise CookieInputError(
        f'Cookie contains a non-ASCII character ({bad[0]!r}) — the copy-paste likely '
        'picked up formatting from the source (smart quotes, browser UI chrome). '
        'Re-copy the raw Cookie header value.')


def _normalize_cookie_input(text: str) -> str:
    """Accept a bare cookie string, a full request-headers dump (Firefox's
    'Copy Request Headers'), a 'Copy as cURL' command, or the JSON that
    Firefox's Cookies tab produces via 'Copy All'
    ({"Request Cookies": {name: value, ...}}), and extract just the Cookie
    header value.

    Raises CookieInputError if the extracted value contains non-ASCII
    characters — see _reject_non_ascii."""
    import json
    import re
    text = text.strip()
    # Firefox Network panel > Cookies tab > Copy All: JSON object
    if text.startswith('{'):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            for key in data:
                if key.lower().replace(' ', '') == 'requestcookies' and isinstance(data[key], dict):
                    data = data[key]
                    break
            pairs = {k: v for k, v in data.items() if isinstance(v, str)}
            if pairs:
                return _reject_non_ascii('; '.join(f'{k}={v}' for k, v in pairs.items()))
    # A "Cookie: ..." line inside a header dump or a curl -H argument
    m = re.search(r'(?:^|\n)\s*[Cc]ookie:\s*(.+)', text)
    if m:
        return _reject_non_ascii(m.group(1).strip().strip('\'"'))
    m = re.search(r'''-H\s+(['"])[Cc]ookie:\s*(.*?)\1''', text)
    if m:
        return _reject_non_ascii(m.group(2).strip())
    # curl's -b / --cookie flag
    m = re.search(r'''(?:--cookie|-b)\s+(['"])(.*?)\1''', text)
    if m:
        return _reject_non_ascii(m.group(2).strip())
    return _reject_non_ascii(text)


def _extract_user_agent(text: str) -> str | None:
    """Pull the User-Agent line out of the same paste the cookie came from,
    when it's a full header dump or curl command. Cloudflare validates
    cf_clearance against the User-Agent that solved the challenge, so
    replaying it under the scraper's own hardcoded UA gets re-challenged
    even with an otherwise-valid cookie — using the browser's own UA for
    that domain avoids the mismatch. Best-effort: returns None (fall back
    to the default UA) rather than raising, since a missing or slightly
    mangled UA is no worse than today's fixed one."""
    import re
    text = text.strip()
    m = re.search(r'(?:^|\n)\s*User-Agent:\s*(.+)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'''-H\s+(['"])User-Agent:\s*(.*?)\1''', text, re.IGNORECASE)
        if not m:
            return None
        value = m.group(2)
    else:
        value = m.group(1)
    value = ''.join(c for c in value.strip().strip('\'"') if ord(c) < 128).strip()
    return value or None


@bp.put('/cookies')
def put_cookie():
    body = request.json or {}
    domain = (body.get('domain') or '').strip().lower()
    if domain.startswith('www.'):
        domain = domain[4:]
    raw = body.get('cookie') or ''
    try:
        cookie = _normalize_cookie_input(raw)
    except CookieInputError as e:
        return jsonify({'error': str(e)}), 400
    if domain not in KNOWN_SITES:
        return jsonify({'error': f'unknown domain: {domain}'}), 400
    user_agent = _extract_user_agent(raw)
    db = get_db()
    if cookie:
        db.execute(
            'INSERT INTO site_cookies(domain, cookie, user_agent, updated_at) VALUES (?,?,?,?)'
            ' ON CONFLICT(domain) DO UPDATE SET cookie=excluded.cookie,'
            ' user_agent=COALESCE(excluded.user_agent, site_cookies.user_agent),'
            ' updated_at=excluded.updated_at',
            (domain, cookie, user_agent, int(time.time())),
        )
    else:
        db.execute('DELETE FROM site_cookies WHERE domain=?', (domain,))
    db.commit()
    return jsonify({'success': True})


@bp.get('/tags')
def list_site_tags():
    rows = get_db().execute(
        'SELECT name, COUNT(*) AS count FROM fic_site_tags'
        ' GROUP BY name ORDER BY count DESC, name').fetchall()
    return jsonify([{'name': r['name'], 'count': r['count']} for r in rows])


@bp.get('/folders')
def list_folders():
    rows = get_db().execute(
        'SELECT f.id, f.name, f.position, f.created_at, f.updated_at,'
        ' COUNT(i.fic_id) AS fic_count'
        ' FROM fic_folders f'
        ' LEFT JOIN fic_folder_items i ON i.folder_id = f.id'
        ' GROUP BY f.id ORDER BY f.position ASC, f.created_at ASC').fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.put('/folders/order')
def reorder_folders():
    """Persist a full folder ordering — `ids` must list every folder exactly
    once; positions are assigned from the list order."""
    ids = (request.json or {}).get('ids')
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return jsonify({'error': 'ids (list of folder ids) required'}), 400
    db = get_db()
    existing = {r['id'] for r in db.execute('SELECT id FROM fic_folders')}
    if len(ids) != len(existing) or set(ids) != existing:
        return jsonify({'error': 'ids must contain every folder id exactly once'}), 400
    now = int(time.time())
    db.executemany(
        'UPDATE fic_folders SET position=?, updated_at=? WHERE id=?',
        [(pos, now, folder_id) for pos, folder_id in enumerate(ids)])
    db.commit()
    return jsonify({'success': True})


@bp.post('/folders')
def create_folder():
    name = ((request.json or {}).get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    folder_id = str(ULID())
    now = int(time.time())
    db = get_db()
    try:
        db.execute(
            'INSERT INTO fic_folders(id, name, position, created_at, updated_at)'
            ' VALUES (?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM fic_folders),?,?)',
            (folder_id, name, now, now))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Folder name already exists'}), 409
    return jsonify({'id': folder_id}), 201


@bp.patch('/folders/<folder_id>')
def rename_folder(folder_id):
    name = ((request.json or {}).get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    db = get_db()
    try:
        cur = db.execute(
            'UPDATE fic_folders SET name=?, updated_at=? WHERE id=?',
            (name, int(time.time()), folder_id))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Folder name already exists'}), 409
    if cur.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


@bp.delete('/folders/<folder_id>')
def delete_folder(folder_id):
    db = get_db()
    db.execute('DELETE FROM fic_folders WHERE id=?', (folder_id,))
    db.commit()
    return jsonify({'success': True})


@bp.post('/<fic_id>/folders')
def add_fic_to_folder(fic_id):
    folder_id = (request.json or {}).get('folderId')
    if not folder_id:
        return jsonify({'error': 'folderId required'}), 400
    db = get_db()
    if not db.execute('SELECT id FROM fics WHERE id=?', (fic_id,)).fetchone():
        return jsonify({'error': 'Fic not found'}), 404
    if not db.execute('SELECT id FROM fic_folders WHERE id=?', (folder_id,)).fetchone():
        return jsonify({'error': 'Folder not found'}), 404
    db.execute(
        'INSERT OR IGNORE INTO fic_folder_items(folder_id, fic_id, created_at) VALUES (?,?,?)',
        (folder_id, fic_id, int(time.time())))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<fic_id>/folders/<folder_id>')
def remove_fic_from_folder(fic_id, folder_id):
    db = get_db()
    db.execute(
        'DELETE FROM fic_folder_items WHERE folder_id=? AND fic_id=?',
        (folder_id, fic_id))
    db.commit()
    return jsonify({'success': True})


@bp.get('/<fic_id>')
def get_fic(fic_id):
    row = get_db().execute('SELECT * FROM fics WHERE id=?', (fic_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_attach_library_meta(_attach_progress([row_to_dict(row)]))[0])


@bp.post('/<fic_id>/opened')
def mark_fic_opened(fic_id):
    db = get_db()
    cur = db.execute(
        'UPDATE fics SET last_opened_at=? WHERE id=?',
        (int(time.time()), fic_id),
    )
    if cur.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<fic_id>')
def delete_fic(fic_id):
    download.cancel_progress(fic_id)
    db = get_db()
    db.execute('DELETE FROM fics WHERE id=?', (fic_id,))
    db.commit()
    storage.delete_fic_dir(fic_id)
    return jsonify({'success': True})


@bp.get('/<fic_id>/chapters')
def list_chapters(fic_id):
    rows = get_db().execute(
        f'SELECT {_CHAPTER_LIST_COLS}, r.chapter_id IS NOT NULL AS is_read'
        ' FROM fic_chapters c'
        ' LEFT JOIN fic_chapter_reads r ON r.chapter_id = c.id'
        ' WHERE c.fic_id=?'
        " ORDER BY CASE WHEN LOWER(c.category) IN ('threadmarks','chapters') THEN 0 ELSE 1 END,"
        ' c.category, c.position',
        (fic_id,),
    ).fetchall()
    dicts = [row_to_dict(r) for r in rows]
    for d in dicts:
        d['isRead'] = bool(d['isRead'])
    return jsonify(dicts)


@bp.get('/chapters/<chapter_id>')
def get_chapter(chapter_id):
    row = get_db().execute(
        'SELECT * FROM fic_chapters WHERE id=?', (chapter_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


def _start_import_bg(fic_id: str, ref: xenforo.ThreadRef) -> None:
    threading.Thread(target=download.run_import, args=(fic_id, ref), daemon=True).start()


def _start_drain_bg() -> None:
    download.start_drain()


def _start_watch_scan_bg(domain: str) -> None:
    threading.Thread(target=download.run_watched_scan, args=(domain,), daemon=True).start()


@bp.post('/import')
def import_from_url():
    body = request.json or {}
    url = (body.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'url required'}), 400
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'invalid url'}), 400
    try:
        ref = xenforo.resolve_thread_ref(url, download._fetch)
    except UnsupportedUrlError as e:
        return jsonify({'error': str(e)}), 422
    except FetchBlockedError as e:
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        return jsonify({'error': f'Could not resolve that URL: {e}'}), 422

    db = get_db()
    existing = db.execute(
        'SELECT id, download_status, chapter_count FROM fics WHERE site=? AND thread_id=?',
        (ref.domain, ref.thread_id),
    ).fetchone()
    if existing:
        # A failed or empty previous import restarts instead of being
        # reported as already present. The update path resumes correctly
        # (dedupes on post id, continues positions).
        broken = existing['download_status'] == 'error' or existing['chapter_count'] == 0
        if broken and not download.is_active(existing['id']):
            db.execute('UPDATE fics SET update_pending=1, download_error=NULL WHERE id=?',
                       (existing['id'],))
            db.commit()
            _start_drain_bg()
            return jsonify({'id': existing['id'], 'restarted': True}), 202
        return jsonify({'id': existing['id'], 'alreadyExists': True})

    fic_id = str(ULID())
    now = int(time.time())
    placeholder = ref.slug.replace('-', ' ').strip() or 'Importing…'
    db.execute(
        'INSERT INTO fics(id, title, source_type, source_url, site, thread_id,'
        ' download_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (fic_id, placeholder, 'xenforo', ref.thread_url, ref.domain,
         ref.thread_id, 'downloading', now, now),
    )
    db.commit()
    download.start_progress(fic_id, 'index')
    _start_import_bg(fic_id, ref)
    return jsonify({'id': fic_id}), 202


@bp.get('/<fic_id>/status')
def import_status(fic_id):
    progress = download.get_progress(fic_id)
    return jsonify(progress or {'done': True})


@bp.post('/<fic_id>/check-updates')
def check_updates(fic_id):
    """Toggle the fic's spot in the serial update queue. Updates are never
    run directly — the drain worker fetches one fic at a time to stay polite
    to the forums — so queueing is cheap and a mis-click can be undone by
    clicking again before the worker gets there.

    `{"deep": true}` asks for the slow pass that re-reads every saved chapter
    and rewrites the ones the author edited. Queuing a deep check over a
    pending shallow one upgrades it rather than cancelling."""
    # silent: the button posts with no body at all for a shallow check.
    deep = bool((request.get_json(silent=True) or {}).get('deep'))
    db = get_db()
    row = db.execute(
        'SELECT source_type, update_pending, deep_pending, download_status'
        ' FROM fics WHERE id=?', (fic_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['source_type'] != 'xenforo':
        return jsonify({'error': 'Only forum fics can be updated'}), 400
    if download.is_active(fic_id) or row['download_status'] == 'downloading':
        return jsonify({'error': 'A download is already running for this fic'}), 409
    if row['update_pending'] and not (deep and not row['deep_pending']):
        db.execute('UPDATE fics SET update_pending=0, deep_pending=0 WHERE id=?', (fic_id,))
        db.commit()
        return jsonify({'id': fic_id, 'queued': False})
    db.execute('UPDATE fics SET update_pending=1, deep_pending=? WHERE id=?',
               (1 if deep else 0, fic_id))
    db.commit()
    _start_drain_bg()
    return jsonify({'id': fic_id, 'queued': True, 'deep': deep}), 202


@bp.post('/refresh-alerts')
def refresh_alerts():
    """Scan page 1 of each cookie'd site's alerts page and queue updates for
    the unique threads mentioned: library fics get flagged, unknown threads
    get a placeholder fic that the drain worker imports.

    A fic is no longer skipped for having been fetched more recently than its
    alert. That comparison assumed an alert is the only way a thread changes,
    but XenForo raises none when an author edits an existing post — so
    "checked since the alert" regularly meant "reported up to date while a
    revised chapter sat unread". A check on an unchanged fic is cheap."""
    db = get_db()
    domains = [
        r['domain'] for r in
        db.execute('SELECT domain FROM site_cookies ORDER BY domain').fetchall()
        if r['domain'] in KNOWN_SITES
    ]
    if not domains:
        return jsonify({'error': 'No site cookies configured — paste your forum'
                        ' session cookies in Settings → Fanfic site cookies first'}), 400

    errors: dict[str, str] = {}
    alerts_seen = 0
    # (site, thread_id) -> (ref, newest alert timestamp)
    newest: dict[tuple[str, str], tuple[xenforo.ThreadRef, int | None]] = {}
    for domain in domains:
        try:
            items = download.fetch_alerts(domain)
        except Exception as e:
            errors[domain] = str(e)
            continue
        alerts_seen += len(items)
        for item in items:
            key = (item.ref.domain, item.ref.thread_id)
            prev = newest.get(key)
            if prev is None or (item.alert_at or 0) > (prev[1] or 0):
                newest[key] = (item.ref, item.alert_at)

    flagged = new_imports = skipped_active = 0
    now = int(time.time())
    for (site, thread_id), (ref, _alert_at) in newest.items():
        row = db.execute(
            'SELECT id, update_pending, download_status'
            ' FROM fics WHERE site=? AND thread_id=?', (site, thread_id)).fetchone()
        if row:
            if (row['update_pending'] or row['download_status'] == 'downloading'
                    or download.is_active(row['id'])):
                skipped_active += 1
            else:
                db.execute('UPDATE fics SET update_pending=1 WHERE id=?', (row['id'],))
                flagged += 1
        else:
            placeholder = ref.slug.replace('-', ' ').strip() or 'Importing…'
            db.execute(
                'INSERT INTO fics(id, title, source_type, source_url, site, thread_id,'
                ' update_pending, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)',
                (str(ULID()), placeholder, 'xenforo', ref.thread_url, ref.domain,
                 ref.thread_id, now, now))
            new_imports += 1
    db.commit()
    # Always poke the drain worker, even if nothing was freshly flagged here —
    # a prior crash/restart can leave fics stuck at update_pending=1 with no
    # worker running to drain them (nothing else resumes that queue on
    # startup), and start_drain() is a cheap no-op when there's nothing to do.
    _start_drain_bg()
    return jsonify({
        'flagged': flagged, 'newImports': new_imports,
        'skippedActive': skipped_active,
        'alertsSeen': alerts_seen, 'errors': errors,
    })


@bp.post('/scan-watched/<domain>')
def scan_watched(domain):
    """Rare, manual archival scan: walk a site's /watched/threads listing
    page by page and queue anything missing from the library, so old watched
    threads that will never generate a fresh alert still get imported. Runs
    in the background (backend/fanfic/download.py:run_watched_scan) and is
    resumable across restarts via the fanfic_watched_scans checkpoint."""
    domain = domain.lower()
    if domain.startswith('www.'):
        domain = domain[4:]
    if domain not in KNOWN_SITES:
        return jsonify({'error': f'unknown domain: {domain}'}), 400
    has_cookie = get_db().execute(
        'SELECT 1 FROM site_cookies WHERE domain=?', (domain,)).fetchone()
    if not has_cookie:
        return jsonify({'error': 'No site cookie configured — paste your forum'
                        ' session cookie in Settings → Fanfic site cookies first'}), 400
    if download.is_watched_scan_active(domain):
        return jsonify({'error': 'A watched-threads scan is already running for this site'}), 409
    _start_watch_scan_bg(domain)
    return jsonify({'started': True}), 202


def _insert_book(book, fic_id: str) -> str:
    """Persist an ImportedBook (epub/docx) with its chapters."""
    db = get_db()
    now = int(time.time())
    from backend.fanfic.sanitize import count_words, html_to_text
    total_words = 0
    for position, (title, html) in enumerate(book.chapters, start=1):
        text = html_to_text(html)
        words = count_words(text)
        total_words += words
        db.execute(
            'INSERT INTO fic_chapters(id, fic_id, position, title, category,'
            ' content_html, content_text, word_count, created_at, updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?)',
            (str(ULID()), fic_id, position, title, 'chapters', html, text, words, now, now),
        )
    db.execute(
        'UPDATE fics SET title=?, author=?, description=?, cover_path=?,'
        ' word_count=?, chapter_count=?, updated_at=? WHERE id=?',
        (book.title, book.author, book.description, book.cover_path,
         total_words, len(book.chapters), now, fic_id),
    )
    db.commit()
    return fic_id


@bp.post('/upload')
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Missing file'}), 400
    upload = request.files['file']
    filename = upload.filename or ''
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ('epub', 'docx', 'pdf'):
        return jsonify({'error': 'Unsupported file type — use .epub, .docx, or .pdf'}), 400

    data = upload.read()
    if not data:
        return jsonify({'error': 'Empty file'}), 400

    fic_id = str(ULID())
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO fics(id, title, source_type, created_at, updated_at) VALUES (?,?,?,?,?)',
        (fic_id, filename, ext, now, now),
    )
    db.commit()

    try:
        if ext == 'pdf':
            pdf = storage.pdf_path(fic_id)
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(data)
            title = filename.rsplit('.', 1)[0] or 'Untitled'
            db.execute('UPDATE fics SET title=? WHERE id=?', (title, fic_id))
            db.commit()
        else:
            if ext == 'epub':
                from backend.fanfic.epub import import_epub
                book = import_epub(data, fic_id, filename)
            else:
                from backend.fanfic.docx import import_docx
                book = import_docx(data, fic_id, filename)
            _insert_book(book, fic_id)
    except Exception as e:
        db.execute('DELETE FROM fics WHERE id=?', (fic_id,))
        db.commit()
        storage.delete_fic_dir(fic_id)
        return jsonify({'error': f'Could not import {filename}: {e}'}), 422

    row = db.execute(f'SELECT {_LIST_COLS} FROM fics WHERE id=?', (fic_id,)).fetchone()
    return jsonify({'id': fic_id, 'fic': row_to_dict(row)}), 201


@bp.get('/<fic_id>/images/<filename>')
def serve_image(fic_id, filename):
    path = storage.safe_image_path(fic_id, filename)
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, max_age=31536000)


@bp.get('/<fic_id>/pdf')
def serve_pdf(fic_id):
    path = storage.pdf_path(fic_id)
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype='application/pdf', max_age=3600)


@bp.post('/<fic_id>/progress')
def save_reading_progress(fic_id):
    body = request.json or {}
    chapter_id = body.get('chapterId')
    db = get_db()
    if chapter_id:
        ch = db.execute(
            'SELECT id FROM fic_chapters WHERE id=? AND fic_id=?',
            (chapter_id, fic_id)).fetchone()
        if not ch:
            return jsonify({'error': 'Chapter not found in this fic'}), 404
    now = int(time.time())
    db.execute(
        'UPDATE fics SET last_read_chapter_id=?, last_opened_at=? WHERE id=?',
        (chapter_id, now, fic_id),
    )
    if chapter_id:
        db.execute(
            'INSERT OR IGNORE INTO fic_chapter_reads(chapter_id, fic_id, created_at)'
            ' VALUES (?,?,?)',
            (chapter_id, fic_id, now))
    db.commit()
    return jsonify({'success': True})


@bp.post('/<fic_id>/read')
def set_chapters_read(fic_id):
    body = request.json or {}
    chapter_ids = body.get('chapterIds')
    read = body.get('read')
    if not isinstance(chapter_ids, list) or not chapter_ids or not isinstance(read, bool):
        return jsonify({'error': 'chapterIds (non-empty list) and read (boolean) required'}), 400
    db = get_db()
    placeholders = ','.join('?' * len(chapter_ids))
    owned = {r['id'] for r in db.execute(
        f'SELECT id FROM fic_chapters WHERE fic_id=? AND id IN ({placeholders})',
        (fic_id, *chapter_ids))}
    if owned != set(chapter_ids):
        return jsonify({'error': 'Chapter not found in this fic'}), 404
    if read:
        now = int(time.time())
        db.executemany(
            'INSERT OR IGNORE INTO fic_chapter_reads(chapter_id, fic_id, created_at)'
            ' VALUES (?,?,?)',
            [(cid, fic_id, now) for cid in chapter_ids])
    else:
        db.execute(
            f'DELETE FROM fic_chapter_reads WHERE fic_id=? AND chapter_id IN ({placeholders})',
            (fic_id, *chapter_ids))
    db.commit()
    count = db.execute(
        'SELECT COUNT(*) AS n FROM fic_chapter_reads WHERE fic_id=?', (fic_id,)).fetchone()
    return jsonify({'success': True, 'readCount': count['n']})


_BOOKMARK_TYPES = {'favorite', 'continue'}


@bp.get('/<fic_id>/bookmarks')
def list_bookmarks(fic_id):
    db = get_db()
    rows = db.execute(
        'SELECT b.id, b.fic_id, b.chapter_id, b.type, b.scroll_position, b.created_at,'
        ' c.title AS chapter_title'
        ' FROM fic_bookmarks b JOIN fic_chapters c ON c.id = b.chapter_id'
        ' WHERE b.fic_id=? ORDER BY b.created_at DESC',
        (fic_id,)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/<fic_id>/bookmarks')
def create_bookmark(fic_id):
    body = request.json or {}
    chapter_id = body.get('chapterId')
    bookmark_type = body.get('type')
    scroll_position = body.get('scrollPosition', 0)
    if bookmark_type not in _BOOKMARK_TYPES:
        return jsonify({'error': "type must be 'favorite' or 'continue'"}), 400
    if not chapter_id:
        return jsonify({'error': 'chapterId required'}), 400
    if not isinstance(scroll_position, (int, float)):
        return jsonify({'error': 'scrollPosition must be a number'}), 400
    db = get_db()
    ch = db.execute(
        'SELECT id FROM fic_chapters WHERE id=? AND fic_id=?',
        (chapter_id, fic_id)).fetchone()
    if not ch:
        return jsonify({'error': 'Chapter not found in this fic'}), 404
    scroll_position = max(0.0, min(1.0, float(scroll_position)))
    if bookmark_type == 'continue':
        db.execute(
            "DELETE FROM fic_bookmarks WHERE fic_id=? AND type='continue'",
            (fic_id,))
    bookmark_id = str(ULID())
    now = int(time.time())
    db.execute(
        'INSERT INTO fic_bookmarks(id, fic_id, chapter_id, type, scroll_position, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (bookmark_id, fic_id, chapter_id, bookmark_type, scroll_position, now))
    db.commit()
    row = db.execute(
        'SELECT b.id, b.fic_id, b.chapter_id, b.type, b.scroll_position, b.created_at,'
        ' c.title AS chapter_title'
        ' FROM fic_bookmarks b JOIN fic_chapters c ON c.id = b.chapter_id'
        ' WHERE b.id=?',
        (bookmark_id,)).fetchone()
    return jsonify(row_to_dict(row))


@bp.delete('/bookmarks/<bookmark_id>')
def delete_bookmark(bookmark_id):
    db = get_db()
    row = db.execute(
        'SELECT id FROM fic_bookmarks WHERE id=?', (bookmark_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM fic_bookmarks WHERE id=?', (bookmark_id,))
    db.commit()
    return jsonify({'success': True})


@bp.patch('/<fic_id>/review')
def save_review(fic_id):
    body = request.json or {}
    sets = []
    params: list = []
    if 'rating' in body:
        rating = body['rating']
        if rating is not None and (not isinstance(rating, int) or isinstance(rating, bool)
                                   or not 1 <= rating <= 5):
            return jsonify({'error': 'rating must be null or an integer from 1 to 5'}), 400
        sets.append('rating=?')
        params.append(rating)
    if 'review' in body:
        review = body['review']
        if review is not None and not isinstance(review, str):
            return jsonify({'error': 'review must be null or a string'}), 400
        sets.append('review=?')
        params.append(review.strip() or None if review else None)
    if not sets:
        return jsonify({'error': 'nothing to update'}), 400
    db = get_db()
    cur = db.execute(
        f"UPDATE fics SET {', '.join(sets)}, updated_at=? WHERE id=?",
        (*params, int(time.time()), fic_id))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


@bp.post('/<fic_id>/journal-link')
def link_journal(fic_id):
    body = request.json or {}
    entry_id = body.get('journalEntryId')
    chapter_id = body.get('chapterId')
    if not entry_id:
        return jsonify({'error': 'journalEntryId required'}), 400
    db = get_db()
    if not db.execute('SELECT id FROM fics WHERE id=?', (fic_id,)).fetchone():
        return jsonify({'error': 'Fic not found'}), 404
    if not db.execute('SELECT id FROM journal_entries WHERE id=?', (entry_id,)).fetchone():
        return jsonify({'error': 'Journal entry not found'}), 404
    if chapter_id:
        ch = db.execute(
            'SELECT id FROM fic_chapters WHERE id=? AND fic_id=?',
            (chapter_id, fic_id)).fetchone()
        if not ch:
            return jsonify({'error': 'Chapter not found in this fic'}), 404
    existing = db.execute(
        'SELECT id FROM journal_entry_fic_refs'
        ' WHERE journal_entry_id=? AND fic_id=? AND chapter_id IS ?',
        (entry_id, fic_id, chapter_id),
    ).fetchone()
    if existing:
        return jsonify({'id': existing['id']})
    link_id = str(ULID())
    db.execute(
        'INSERT INTO journal_entry_fic_refs(id, journal_entry_id, fic_id, chapter_id, created_at)'
        ' VALUES (?,?,?,?,?)',
        (link_id, entry_id, fic_id, chapter_id, int(time.time())),
    )
    db.commit()
    return jsonify({'id': link_id}), 201


@bp.delete('/<fic_id>/journal-link/<entry_id>')
def unlink_journal(fic_id, entry_id):
    chapter_id = request.args.get('chapterId')
    db = get_db()
    db.execute(
        'DELETE FROM journal_entry_fic_refs'
        ' WHERE journal_entry_id=? AND fic_id=? AND chapter_id IS ?',
        (entry_id, fic_id, chapter_id or None),
    )
    db.commit()
    return jsonify({'success': True})
