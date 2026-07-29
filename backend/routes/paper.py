import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import build_update, get_db, row_to_dict
from backend.paper import storage

bp = Blueprint('paper', __name__, url_prefix='/api/paper')


def _page_image_url(page) -> str | None:
    """URL for a page's PNG snapshot, cache-busted by updated_at, or None if the
    page hasn't been saved with a snapshot yet."""
    if not page['image_path']:
        return None
    return f"/api/paper/pages/{page['id']}/image?v={page['updated_at']}"


# The "journal day" runs 4am->4am (local time). A paper flagged for the journal
# stays in the explorer until the next 4am boundary passes, then moves — computed
# lazily so no scheduler is needed and it survives restarts.

def _cutoff_4am(now_ts: int) -> int:
    """The most recent 4am (local) at or before now. A paper whose
    archive_requested_at is strictly before this has had a 4am tick over since it
    was flagged, so it now belongs in the journal."""
    dt = datetime.fromtimestamp(now_ts)
    boundary = dt.replace(hour=4, minute=0, second=0, microsecond=0)
    if dt < boundary:
        boundary -= timedelta(days=1)
    return int(boundary.timestamp())


def _journal_day(ts: int) -> str:
    """The local calendar date (YYYY-MM-DD) of the journal day a timestamp falls
    in — before 4am counts as the previous day's late-night session."""
    dt = datetime.fromtimestamp(ts)
    if dt.hour < 4:
        dt -= timedelta(days=1)
    return dt.strftime('%Y-%m-%d')


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
        d['firstPageImageUrl'] = _page_image_url(first) if first else None
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
            'journalDate': _journal_day(p['archive_requested_at']),
            'archivedAt': _iso(p['archive_requested_at']),
            'pages': [
                {'id': pg['id'], 'imageUrl': _page_image_url(pg)} for pg in pages
            ],
        })
    return jsonify(result)


@bp.post('')
def create_paper():
    body = request.get_json(silent=True) or {}
    now = int(time.time())
    paper_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO papers(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (paper_id, body.get('title', '').strip(), now, now),
    )
    # Every paper starts with one blank page.
    db.execute(
        'INSERT INTO paper_pages(id, paper_id, position, created_at, updated_at) VALUES (?,?,?,?,?)',
        (str(ULID()), paper_id, 0, now, now),
    )
    db.commit()
    return jsonify({'id': paper_id}), 201


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
        {'id': pg['id'], 'position': pg['position'], 'imageUrl': _page_image_url(pg)}
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
    page_id = str(ULID())
    db.execute(
        'INSERT INTO paper_pages(id, paper_id, position, created_at, updated_at) VALUES (?,?,?,?,?)',
        (page_id, paper_id, row['next_pos'], now, now),
    )
    db.execute('UPDATE papers SET updated_at=? WHERE id=?', (now, paper_id))
    db.commit()
    return jsonify({'id': page_id, 'position': row['next_pos']}), 201


@bp.get('/pages/<page_id>')
def get_page(page_id):
    row = get_db().execute(
        'SELECT strokes, width, height FROM paper_pages WHERE id=?', (page_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'strokes': row['strokes'], 'width': row['width'], 'height': row['height']})


@bp.put('/pages/<page_id>')
def save_page(page_id):
    db = get_db()
    page = db.execute('SELECT paper_id FROM paper_pages WHERE id=?', (page_id,)).fetchone()
    if not page:
        return jsonify({'error': 'Not found'}), 404
    paper_id = page['paper_id']

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
    updates: dict = {'strokes': strokes, 'width': width, 'height': height, 'updated_at': now}
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
