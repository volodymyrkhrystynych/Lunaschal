"""Ideas: the app's own feature backlog.

An idea is captured by voice or typing, kept as raw_content (never overwritten)
alongside AI-cleaned content, and developed against the repo-context snapshot
and the research wiki. Sketches are Paper *pages* borrowed into an idea; the
caption, not the image, is what the agent reads — see idea_sketches in
backend/db/schema.sql.
"""
import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import build_update, get_db, row_to_dict
from backend.routes.paper import page_image_url
from backend.tags import tags_json

bp = Blueprint('ideas', __name__, url_prefix='/api/ideas')

STATUSES = ('new', 'researching', 'ready', 'planned', 'building', 'shipped', 'parked')

# Columns the list endpoint returns: everything except the two body columns,
# which are only needed once an idea is opened.
_LIST_COLUMNS = 'id, title, status, tags, created_at, updated_at'

# --- Ideas ---

@bp.get('')
def list_ideas():
    db = get_db()
    rows = db.execute(
        f'SELECT {_LIST_COLUMNS} FROM ideas ORDER BY updated_at DESC'
    ).fetchall()
    counts = {
        r['idea_id']: r['n']
        for r in db.execute(
            'SELECT idea_id, COUNT(*) AS n FROM idea_sketches GROUP BY idea_id'
        ).fetchall()
    }
    result = []
    for r in rows:
        d = row_to_dict(r)
        d['sketchCount'] = counts.get(r['id'], 0)
        result.append(d)
    return jsonify(result)


@bp.post('')
def create_idea():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    raw_content = body.get('rawContent') or ''
    if not title and not raw_content.strip():
        return jsonify({'error': 'title or rawContent required'}), 400
    now = int(time.time())
    id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, tags, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?,?)',
        (id, title, raw_content, '', 'new', tags_json(body.get('tags')), now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


@bp.get('/<idea_id>')
def get_idea(idea_id):
    row = get_db().execute('SELECT * FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.patch('/<idea_id>')
def update_idea(idea_id):
    body = request.json or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'title' in body:
        updates['title'] = (body['title'] or '').strip()
    if 'rawContent' in body:
        updates['raw_content'] = body['rawContent']
    if 'content' in body:
        updates['content'] = body['content']
    if 'status' in body:
        if body['status'] not in STATUSES:
            return jsonify({'error': 'invalid status'}), 400
        updates['status'] = body['status']
    if 'tags' in body:
        updates['tags'] = tags_json(body['tags'])
    db = get_db()
    build_update(db, 'ideas', updates, 'id=?', (idea_id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<idea_id>')
def delete_idea(idea_id):
    db = get_db()
    db.execute('DELETE FROM ideas WHERE id=?', (idea_id,))
    db.commit()
    return jsonify({'success': True})


@bp.post('/voice')
def create_from_voice():
    """Save a transcript as a new idea, mirroring the journal's voice path: the
    transcript lands in raw_content untouched and titling/cleanup happens later."""
    body = request.json or {}
    raw_content = (body.get('rawContent') or '').strip()
    if not raw_content:
        return jsonify({'error': 'rawContent required'}), 400
    now = int(time.time())
    id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (id, '', raw_content, '', 'new', now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


# --- Sketches (Paper pages borrowed into an idea) ---

@bp.get('/<idea_id>/sketches')
def list_sketches(idea_id):
    db = get_db()
    rows = db.execute(
        'SELECT s.*, p.image_path, p.updated_at AS page_updated_at, p.paper_id'
        ' FROM idea_sketches s JOIN paper_pages p ON p.id = s.page_id'
        ' WHERE s.idea_id=? ORDER BY s.position ASC',
        (idea_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        # Drop the joined page internals; the client only needs the image URL.
        d.pop('imagePath', None)
        d.pop('pageUpdatedAt', None)
        d['imageUrl'] = page_image_url(
            {'id': r['page_id'], 'image_path': r['image_path'],
             'updated_at': r['page_updated_at']}
        )
        result.append(d)
    return jsonify(result)


@bp.post('/<idea_id>/sketches')
def add_sketch(idea_id):
    body = request.json or {}
    page_id = (body.get('pageId') or '').strip()
    if not page_id:
        return jsonify({'error': 'pageId required'}), 400
    db = get_db()
    if not db.execute('SELECT 1 FROM ideas WHERE id=?', (idea_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    if not db.execute('SELECT 1 FROM paper_pages WHERE id=?', (page_id,)).fetchone():
        return jsonify({'error': 'page not found'}), 404
    now = int(time.time())
    position = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM idea_sketches WHERE idea_id=?',
        (idea_id,),
    ).fetchone()['next_pos']
    id = str(ULID())
    db.execute(
        'INSERT INTO idea_sketches(id, idea_id, page_id, caption, position, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (id, idea_id, page_id, body.get('caption') or '', position, now),
    )
    db.execute('UPDATE ideas SET updated_at=? WHERE id=?', (now, idea_id))
    db.commit()
    return jsonify({'id': id}), 201


@bp.patch('/sketches/<sketch_id>')
def update_sketch(sketch_id):
    body = request.json or {}
    updates: dict = {}
    if 'caption' in body:
        updates['caption'] = body['caption'] or ''
    if 'position' in body:
        updates['position'] = int(body['position'])
    if not updates:
        return jsonify({'success': True})
    db = get_db()
    build_update(db, 'idea_sketches', updates, 'id=?', (sketch_id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/sketches/<sketch_id>')
def delete_sketch(sketch_id):
    db = get_db()
    db.execute('DELETE FROM idea_sketches WHERE id=?', (sketch_id,))
    db.commit()
    return jsonify({'success': True})



@bp.get('/paper-pages')
def list_paper_pages():
    """Flat feed of every Paper page that has a snapshot, for the sketch picker.

    Flat rather than grouped by paper because the picker is a single scrollable
    grid of pages — the paper title is just a caption on each tile.
    """
    rows = get_db().execute(
        'SELECT p.id, p.paper_id, p.position, p.image_path, p.updated_at,'
        ' d.title AS paper_title'
        ' FROM paper_pages p JOIN papers d ON d.id = p.paper_id'
        ' WHERE p.image_path IS NOT NULL'
        ' ORDER BY d.updated_at DESC, p.position ASC'
    ).fetchall()
    return jsonify([
        {
            'pageId': r['id'],
            'paperId': r['paper_id'],
            'paperTitle': r['paper_title'],
            'position': r['position'],
            'imageUrl': page_image_url(r),
        }
        for r in rows
    ])
