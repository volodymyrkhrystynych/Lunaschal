import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import build_update, get_db, row_to_dict
from backend.day_boundary import day_bounds, day_key_for
from backend.imaging import HEIC_EXTS, transcode_to_jpeg
from backend.paper import storage

bp = Blueprint('paper', __name__, url_prefix='/api/paper')


def page_image_url(page) -> str | None:
    """URL for a page's PNG snapshot, cache-busted by updated_at, or None if the
    page hasn't been saved with a snapshot yet."""
    if not page['image_path']:
        return None
    return f"/api/paper/pages/{page['id']}/image?v={page['updated_at']}"


# A paper flagged for the journal stays in the explorer until the next 4am
# boundary passes, then moves — computed lazily off backend.day_boundary so
# no scheduler is needed and it survives restarts.

def _cutoff_4am(now_ts: int) -> int:
    """The most recent 4am (local) at or before now. A paper whose
    archive_requested_at is strictly before this has had a 4am tick over since it
    was flagged, so it now belongs in the journal."""
    return day_bounds(day_key_for(now_ts))[0]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# --- Papers ---

@bp.get('')
def list_papers():
    db = get_db()
    limit = min(int(request.args.get('limit', 60)), 200)
    offset = int(request.args.get('offset', 0))
    cutoff = _cutoff_4am(int(time.time()))
    # Exclude papers that have already moved to the journal (flagged, and a 4am
    # boundary has since passed). Papers flagged after the last 4am stay here,
    # marked pending.
    papers = db.execute(
        'SELECT * FROM papers '
        'WHERE archive_requested_at IS NULL OR archive_requested_at >= ? '
        'ORDER BY updated_at DESC LIMIT ? OFFSET ?',
        (cutoff, limit, offset),
    ).fetchall()
    result = []
    for p in papers:
        first = db.execute(
            'SELECT id, image_path, updated_at FROM paper_pages WHERE paper_id=? ORDER BY position ASC LIMIT 1',
            (p['id'],),
        ).fetchone()
        count = db.execute(
            'SELECT COUNT(*) AS n FROM paper_pages WHERE paper_id=?', (p['id'],)
        ).fetchone()['n']
        d = row_to_dict(p)
        d['pageCount'] = count
        d['firstPageImageUrl'] = page_image_url(first) if first else None
        d['pendingArchive'] = p['archive_requested_at'] is not None
        result.append(d)
    return jsonify(result)


@bp.get('/journal')
def journal_papers():
    """Papers that have moved into the journal, newest first, each with all its
    page thumbnails for the Journal feed's filmstrip."""
    db = get_db()
    cutoff = _cutoff_4am(int(time.time()))
    papers = db.execute(
        'SELECT * FROM papers '
        'WHERE archive_requested_at IS NOT NULL AND archive_requested_at < ? '
        'ORDER BY archive_requested_at DESC',
        (cutoff,),
    ).fetchall()
    result = []
    for p in papers:
        pages = db.execute(
            'SELECT id, image_path, updated_at FROM paper_pages WHERE paper_id=? ORDER BY position ASC',
            (p['id'],),
        ).fetchall()
        result.append({
            'id': p['id'],
            'title': p['title'],
            'journalDate': day_key_for(p['archive_requested_at']),
            'archivedAt': _iso(p['archive_requested_at']),
            'pages': [
                {'id': pg['id'], 'imageUrl': page_image_url(pg)} for pg in pages
            ],
        })
    return jsonify(result)


@bp.post('')
def create_paper():
    body = request.get_json(silent=True) or {}
    now = int(time.time())
    # Client-supplied ULIDs, for both the paper and its first page. Paper is the
    # one feature whose data exists *only* on the tablet it was written on, so
    # starting a new page must not require a server: the ids are minted on the
    # device, the writing begins immediately, and the create replays
    # idempotently whenever the backend is next in reach.
    paper_id = (body.get('id') or '').strip() or str(ULID())
    page_id = (body.get('pageId') or '').strip() or str(ULID())
    db = get_db()
    db.execute(
        'INSERT OR IGNORE INTO papers(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (paper_id, body.get('title', '').strip(), now, now),
    )
    # Every paper starts with one blank page.
    db.execute(
        'INSERT OR IGNORE INTO paper_pages(id, paper_id, position, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (page_id, paper_id, 0, now, now),
    )
    db.commit()
    return jsonify({'id': paper_id, 'pageId': page_id}), 201


@bp.get('/<paper_id>')
def get_paper(paper_id):
    db = get_db()
    paper = db.execute('SELECT * FROM papers WHERE id=?', (paper_id,)).fetchone()
    if not paper:
        return jsonify({'error': 'Not found'}), 404
    pages = db.execute(
        'SELECT id, position, image_path, updated_at FROM paper_pages WHERE paper_id=? ORDER BY position ASC',
        (paper_id,),
    ).fetchall()
    d = row_to_dict(paper)
    d['pages'] = [
        {'id': pg['id'], 'position': pg['position'], 'imageUrl': page_image_url(pg)}
        for pg in pages
    ]
    d['archiveRequested'] = paper['archive_requested_at'] is not None
    return jsonify(d)


@bp.patch('/<paper_id>')
def update_paper(paper_id):
    body = request.get_json(silent=True) or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'title' in body:
        updates['title'] = body['title'].strip()
    if 'archiveRequested' in body:
        updates['archive_requested_at'] = (
            int(time.time()) if body['archiveRequested'] else None
        )
    db = get_db()
    build_update(db, 'papers', updates, 'id=?', (paper_id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<paper_id>')
def delete_paper(paper_id):
    db = get_db()
    db.execute('DELETE FROM papers WHERE id=?', (paper_id,))  # cascades pages
    db.commit()
    storage.delete_paper_dir(paper_id)
    return jsonify({'success': True})


# --- Pages ---

@bp.post('/<paper_id>/pages')
def add_page(paper_id):
    db = get_db()
    paper = db.execute('SELECT id FROM papers WHERE id=?', (paper_id,)).fetchone()
    if not paper:
        return jsonify({'error': 'Not found'}), 404
    row = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM paper_pages WHERE paper_id=?',
        (paper_id,),
    ).fetchone()
    now = int(time.time())
    body = request.get_json(silent=True) or {}
    page_id = (body.get('id') or '').strip() or str(ULID())
    db.execute(
        'INSERT OR IGNORE INTO paper_pages(id, paper_id, position, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (page_id, paper_id, row['next_pos'], now, now),
    )
    db.execute('UPDATE papers SET updated_at=? WHERE id=?', (now, paper_id))
    db.commit()
    return jsonify({'id': page_id, 'position': row['next_pos']}), 201


@bp.get('/pages/<page_id>')
def get_page(page_id):
    db = get_db()
    row = db.execute(
        'SELECT strokes, width, height FROM paper_pages WHERE id=?', (page_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({
        'strokes': row['strokes'],
        'width': row['width'],
        'height': row['height'],
        'images': page_images(db, page_id),
    })


@bp.put('/pages/<page_id>')
def save_page(page_id):
    db = get_db()
    page = db.execute('SELECT paper_id FROM paper_pages WHERE id=?', (page_id,)).fetchone()
    if not page:
        return jsonify({'error': 'Not found'}), 404
    paper_id = page['paper_id']

    # The client uploads strokes as a file part so the payload isn't capped by
    # Werkzeug's max_form_memory_size (500kB) — a densely written page exceeds
    # that and gets a 413. Fall back to a plain field for older clients.
    strokes_part = request.files.get('strokes')
    if strokes_part is not None:
        strokes = strokes_part.read().decode('utf-8', 'replace')
    else:
        strokes = request.form.get('strokes', '[]')
    width = request.form.get('width', type=int)
    height = request.form.get('height', type=int)

    image_path = None
    snapshot = request.files.get('snapshot')
    if snapshot and snapshot.filename:
        path = storage.page_image_path(paper_id, page_id)
        if path is None:
            return jsonify({'error': 'Invalid id'}), 500
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot.save(path)
        image_path = str(path)

    now = int(time.time())
    updates: dict = {'strokes': strokes, 'updated_at': now}
    # Strokes are stored in the page's logical coordinate space, so blanking the
    # size would misplace every stroke on the next load — only write real values.
    if width:
        updates['width'] = width
    if height:
        updates['height'] = height
    if image_path is not None:
        updates['image_path'] = image_path
    build_update(db, 'paper_pages', updates, 'id=?', (page_id,))
    db.execute('UPDATE papers SET updated_at=? WHERE id=?', (now, paper_id))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/pages/<page_id>')
def delete_page(page_id):
    db = get_db()
    row = db.execute('SELECT paper_id, image_path FROM paper_pages WHERE id=?', (page_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM paper_pages WHERE id=?', (page_id,))
    db.execute('UPDATE papers SET updated_at=? WHERE id=?', (int(time.time()), row['paper_id']))
    db.commit()
    if row['image_path']:
        path = storage.resolve_stored_path(row['image_path'])
        if path is not None and path.is_file():
            path.unlink(missing_ok=True)
    return jsonify({'success': True})


@bp.get('/pages/<page_id>/image')
def serve_page_image(page_id):
    row = get_db().execute('SELECT image_path FROM paper_pages WHERE id=?', (page_id,)).fetchone()
    if row is None or not row['image_path']:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['image_path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype='image/png', conditional=True)


# --- pasted images ---

_IMAGE_COLUMNS = (
    'id, page_id, file_path, x, y, width, height, rotation, flipped, locked, '
    'position, created_at, updated_at'
)


def _image_row(row) -> dict:
    d = row_to_dict(row)
    # Cache-busted on updated_at like the page snapshot: the bytes never change
    # for a given id, but a re-upload under the same id would otherwise stick.
    d['url'] = f"/api/paper/images/{d['id']}/file?v={row['updated_at']}"
    # The stored path is server-side detail; the client only ever needs the URL.
    d.pop('filePath', None)
    return d


def page_images(db, page_id: str) -> list[dict]:
    rows = db.execute(
        f'SELECT {_IMAGE_COLUMNS} FROM paper_page_images WHERE page_id=? ORDER BY position, created_at',
        (page_id,),
    ).fetchall()
    return [_image_row(r) for r in rows]


@bp.post('/pages/<page_id>/images')
def add_page_image(page_id):
    db = get_db()
    page = db.execute('SELECT paper_id FROM paper_pages WHERE id=?', (page_id,)).fetchone()
    if not page:
        return jsonify({'error': 'Not found'}), 404

    upload = request.files.get('image')
    if upload is None or not upload.filename:
        return jsonify({'error': 'image file required'}), 400
    ext = storage.resolve_ext(upload.mimetype, upload.filename)
    if ext is None:
        return jsonify({
            'error': f'unsupported image type: {upload.mimetype or upload.filename}'
        }), 400
    # HEIC becomes JPEG here or the picture is dead weight everywhere after:
    # no browser renders it, so the page would carry an image it cannot draw.
    is_heic = ext in HEIC_EXTS
    if is_heic:
        ext = 'jpg'

    for field in ('x', 'y', 'width', 'height'):
        if request.form.get(field, type=float) is None:
            return jsonify({'error': f'{field} required'}), 400
    width = request.form.get('width', type=float)
    height = request.form.get('height', type=float)
    if width <= 0 or height <= 0:
        return jsonify({'error': 'width and height must be positive'}), 400

    # Client-supplied id for a picture pasted offline: the upload is queued, and
    # a replay of it must land the same picture rather than a second copy of it.
    image_id = (request.form.get('id') or '').strip() or str(ULID())
    existing = db.execute(
        f'SELECT {_IMAGE_COLUMNS} FROM paper_page_images WHERE id=?', (image_id,)
    ).fetchone()
    if existing:
        return jsonify(_image_row(existing)), 201
    path = storage.pasted_image_path(page['paper_id'], image_id, ext)
    if path is None:
        return jsonify({'error': 'Invalid id'}), 500
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_heic:
        if not transcode_to_jpeg(upload, path):
            path.unlink(missing_ok=True)
            return jsonify({'error': 'could not read that picture'}), 400
    else:
        # Streamed to disk, not read() into memory: a phone photo is happily
        # several MB and this is the same rule journal attachments follow.
        upload.save(path)

    now = int(time.time())
    next_pos = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS p FROM paper_page_images WHERE page_id=?',
        (page_id,),
    ).fetchone()['p']
    db.execute(
        '''INSERT OR IGNORE INTO paper_page_images(
               id, page_id, file_path, x, y, width, height, position, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (image_id, page_id, str(path), request.form.get('x', type=float),
         request.form.get('y', type=float), width, height, next_pos, now, now),
    )
    db.execute('UPDATE paper_pages SET updated_at=? WHERE id=?', (now, page_id))
    db.commit()
    row = db.execute(
        f'SELECT {_IMAGE_COLUMNS} FROM paper_page_images WHERE id=?', (image_id,)
    ).fetchone()
    return jsonify(_image_row(row)), 201


_IMAGE_FIELDS = {
    'x': 'x', 'y': 'y', 'width': 'width', 'height': 'height', 'rotation': 'rotation',
}


@bp.patch('/images/<image_id>')
def update_page_image(image_id):
    db = get_db()
    row = db.execute(
        'SELECT id, page_id, locked FROM paper_page_images WHERE id=?', (image_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    body = request.json or {}

    updates: dict = {}
    for camel, col in _IMAGE_FIELDS.items():
        if camel in body:
            try:
                updates[col] = float(body[camel])
            except (TypeError, ValueError):
                return jsonify({'error': f'{camel} must be a number'}), 400
    if 'width' in updates and updates['width'] <= 0:
        return jsonify({'error': 'width must be positive'}), 400
    if 'height' in updates and updates['height'] <= 0:
        return jsonify({'error': 'height must be positive'}), 400
    if 'flipped' in body:
        updates['flipped'] = 1 if body['flipped'] else 0
    if 'locked' in body:
        updates['locked'] = 1 if body['locked'] else 0

    # A locked image only accepts being unlocked. The lock exists to stop a
    # stray drag moving a photo, so honouring a geometry write in the same
    # breath would defeat it — and the client can't be the only thing enforcing
    # that, since an in-flight drag can land after the lock.
    if row['locked'] and updates.keys() - {'locked'}:
        return jsonify({'error': 'image is locked'}), 409
    if not updates:
        return jsonify({'error': 'no fields to update'}), 400

    now = int(time.time())
    updates['updated_at'] = now
    build_update(db, 'paper_page_images', updates, 'id=?', (image_id,))
    db.execute('UPDATE paper_pages SET updated_at=? WHERE id=?', (now, row['page_id']))
    db.commit()
    fresh = db.execute(
        f'SELECT {_IMAGE_COLUMNS} FROM paper_page_images WHERE id=?', (image_id,)
    ).fetchone()
    return jsonify(_image_row(fresh))


@bp.delete('/images/<image_id>')
def delete_page_image(image_id):
    db = get_db()
    row = db.execute(
        'SELECT file_path, page_id FROM paper_page_images WHERE id=?', (image_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM paper_page_images WHERE id=?', (image_id,))
    db.execute('UPDATE paper_pages SET updated_at=? WHERE id=?', (int(time.time()), row['page_id']))
    db.commit()
    path = storage.resolve_stored_path(row['file_path'])
    if path is not None and path.is_file():
        path.unlink(missing_ok=True)
    return jsonify({'success': True})


@bp.get('/images/<image_id>/file')
def serve_page_image_file(image_id):
    row = get_db().execute(
        'SELECT file_path FROM paper_page_images WHERE id=?', (image_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['file_path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype=storage.STORED_EXTS.get(path.suffix.lstrip('.').lower(),
                                                            'application/octet-stream'),
                     conditional=True)
