import threading
import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import get_db, row_to_dict, search_fanfic_fts
from backend.fanfic import download, storage, xenforo
from backend.fanfic.download import FetchBlockedError
from backend.fanfic.xenforo import KNOWN_SITES, UnsupportedUrlError

bp = Blueprint('fanfic', __name__, url_prefix='/api/fanfic')

_LIST_COLS = (
    'id, title, author, source_type, source_url, site, cover_path, word_count,'
    ' chapter_count, download_status, download_error, last_read_chapter_id,'
    ' last_checked_at, created_at, updated_at'
)

_CHAPTER_LIST_COLS = 'id, fic_id, position, title, category, word_count, posted_at'


def _attach_progress(dicts: list[dict]) -> list[dict]:
    for d in dicts:
        progress = download.get_progress(d['id'])
        if progress:
            d['downloadProgress'] = progress
    return dicts


@bp.get('')
def list_fics():
    limit = min(int(request.args.get('limit', 100)), 200)
    offset = int(request.args.get('offset', 0))
    rows = get_db().execute(
        f'SELECT {_LIST_COLS} FROM fics ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (limit, offset),
    ).fetchall()
    return jsonify(_attach_progress([row_to_dict(r) for r in rows]))


@bp.get('/search')
def search():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([])
    fts = search_fanfic_fts(query, limit=100)
    if not fts:
        return jsonify([])
    db = get_db()
    id_rank = {r['id']: r['rank'] for r in fts}
    placeholders = ','.join('?' * len(id_rank))
    chapters = db.execute(
        f'SELECT id, fic_id, title FROM fic_chapters WHERE id IN ({placeholders})',
        list(id_rank),
    ).fetchall()
    by_fic: dict[str, dict] = {}
    for ch in sorted(chapters, key=lambda c: id_rank.get(c['id'], 0)):
        entry = by_fic.setdefault(ch['fic_id'], {'rank': id_rank[ch['id']], 'matched': []})
        if len(entry['matched']) < 3:
            entry['matched'].append({'id': ch['id'], 'title': ch['title']})
    if not by_fic:
        return jsonify([])
    placeholders = ','.join('?' * len(by_fic))
    fics = db.execute(
        f'SELECT {_LIST_COLS} FROM fics WHERE id IN ({placeholders})',
        list(by_fic),
    ).fetchall()
    dicts = []
    for row in sorted(fics, key=lambda f: by_fic[f['id']]['rank']):
        d = row_to_dict(row)
        d['matchedChapters'] = by_fic[row['id']]['matched']
        dicts.append(d)
    return jsonify(_attach_progress(dicts))


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


@bp.get('/<fic_id>')
def get_fic(fic_id):
    row = get_db().execute('SELECT * FROM fics WHERE id=?', (fic_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_attach_progress([row_to_dict(row)])[0])


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
        f'SELECT {_CHAPTER_LIST_COLS} FROM fic_chapters WHERE fic_id=?'
        " ORDER BY CASE WHEN LOWER(category) IN ('threadmarks','chapters') THEN 0 ELSE 1 END,"
        ' category, position',
        (fic_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


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
    db.execute(
        'UPDATE fics SET last_read_chapter_id=?, updated_at=? WHERE id=?',
        (chapter_id, int(time.time()), fic_id),
    )
    db.commit()
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
