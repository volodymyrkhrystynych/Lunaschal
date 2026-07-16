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
    ' chapter_count, download_status, download_error, last_read_chapter_id,'
    ' last_checked_at, rating, created_at, updated_at'
)

_CHAPTER_LIST_COLS = 'c.id, c.fic_id, c.position, c.title, c.category, c.word_count, c.posted_at'

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

# The "All" view groups fics by folder order (a fic in several folders counts
# under its earliest-positioned one); fics in no folder sort last.
_FOLDER_GROUP_ORDER = (
    '(SELECT MIN(f.position) FROM fic_folder_items i'
    ' JOIN fic_folders f ON f.id = i.folder_id'
    ' WHERE i.fic_id = fics.id) ASC NULLS LAST'
)


def _attach_progress(dicts: list[dict]) -> list[dict]:
    for d in dicts:
        progress = download.get_progress(d['id'])
        if progress:
            d['downloadProgress'] = progress
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
    # Inside a single folder (or the unsorted view) grouping is meaningless —
    # plain recency there; everywhere else group by folder order first.
    order = _LATEST_ACTIVITY_ORDER if folder_id else f'{_FOLDER_GROUP_ORDER}, {_LATEST_ACTIVITY_ORDER}'
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
    rows = get_db().execute('SELECT domain, updated_at FROM site_cookies').fetchall()
    stored = {r['domain']: r for r in rows}
    return jsonify([
        {
            'domain': domain,
            'hasCookie': domain in stored,
            'updatedAt': row_to_dict(stored[domain])['updatedAt'] if domain in stored else None,
        }
        for domain in sorted(KNOWN_SITES)
    ])


def _normalize_cookie_input(text: str) -> str:
    """Accept a bare cookie string, a full request-headers dump (Firefox's
    'Copy Request Headers'), a 'Copy as cURL' command, or the JSON that
    Firefox's Cookies tab produces via 'Copy All'
    ({"Request Cookies": {name: value, ...}}), and extract just the Cookie
    header value."""
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
                return '; '.join(f'{k}={v}' for k, v in pairs.items())
    # A "Cookie: ..." line inside a header dump or a curl -H argument
    m = re.search(r'(?:^|\n)\s*[Cc]ookie:\s*(.+)', text)
    if m:
        return m.group(1).strip().strip('\'"')
    m = re.search(r'''-H\s+(['"])[Cc]ookie:\s*(.*?)\1''', text)
    if m:
        return m.group(2).strip()
    # curl's -b / --cookie flag
    m = re.search(r'''(?:--cookie|-b)\s+(['"])(.*?)\1''', text)
    if m:
        return m.group(2).strip()
    return text


@bp.put('/cookies')
def put_cookie():
    body = request.json or {}
    domain = (body.get('domain') or '').strip().lower()
    if domain.startswith('www.'):
        domain = domain[4:]
    cookie = _normalize_cookie_input(body.get('cookie') or '')
    if domain not in KNOWN_SITES:
        return jsonify({'error': f'unknown domain: {domain}'}), 400
    db = get_db()
    if cookie:
        db.execute(
            'INSERT INTO site_cookies(domain, cookie, updated_at) VALUES (?,?,?)'
            ' ON CONFLICT(domain) DO UPDATE SET cookie=excluded.cookie, updated_at=excluded.updated_at',
            (domain, cookie, int(time.time())),
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


def _start_update_bg(fic_id: str) -> None:
    threading.Thread(target=download.run_check_updates, args=(fic_id,), daemon=True).start()


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
            db.execute("UPDATE fics SET download_status='downloading', download_error=NULL WHERE id=?",
                       (existing['id'],))
            db.commit()
            download.start_progress(existing['id'], 'updating')
            _start_update_bg(existing['id'])
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
    db = get_db()
    row = db.execute('SELECT source_type FROM fics WHERE id=?', (fic_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['source_type'] != 'xenforo':
        return jsonify({'error': 'Only forum fics can be updated'}), 400
    if download.is_active(fic_id):
        return jsonify({'error': 'A download is already running for this fic'}), 409
    download.start_progress(fic_id, 'updating')
    db.execute("UPDATE fics SET download_status='downloading' WHERE id=?", (fic_id,))
    db.commit()
    _start_update_bg(fic_id)
    return jsonify({'id': fic_id}), 202


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
            ' content_html, content_text, word_count, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?)',
            (str(ULID()), fic_id, position, title, 'chapters', html, text, words, now),
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
        'UPDATE fics SET last_read_chapter_id=?, updated_at=? WHERE id=?',
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
