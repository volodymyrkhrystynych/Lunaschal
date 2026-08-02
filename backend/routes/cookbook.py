import json
import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.ai.recipes import parse_recipe
from backend.htmltext import strip_html
from backend.db.connection import build_update, get_db, row_to_dict, search_recipes_fts
from backend.tags import tag_counts

bp = Blueprint('cookbook', __name__, url_prefix='/api/cookbook')

_MAX_PAGE_CHARS = 15000


@bp.get('')
def list_recipes():
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    tag = request.args.get('tag', '').strip()
    db = get_db()
    if tag:
        rows = db.execute(
            'SELECT * FROM recipes WHERE tags LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (f'%"{tag}"%', limit, offset),
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT * FROM recipes ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (limit, offset),
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/search')
def search():
    query = request.args.get('query', '').strip()
    limit = min(int(request.args.get('limit', 50)), 100)
    if not query:
        return jsonify([])
    fts = search_recipes_fts(query, limit)
    if not fts:
        return jsonify([])
    db = get_db()
    id_rank = {r['id']: r['rank'] for r in fts}
    placeholders = ','.join('?' * len(id_rank))
    rows = db.execute(
        f'SELECT * FROM recipes WHERE id IN ({placeholders})',
        list(id_rank),
    ).fetchall()
    dicts = sorted([row_to_dict(r) for r in rows], key=lambda d: id_rank.get(d['id'], 0))
    return jsonify(dicts)


@bp.get('/tags')
def list_tags():
    rows = get_db().execute('SELECT tags FROM recipes WHERE tags IS NOT NULL').fetchall()
    return jsonify(tag_counts(rows))


@bp.get('/<id>')
def get_recipe(id):
    row = get_db().execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


def _insert_recipe(title: str, content: str, tags: list | None, source_url: str | None = None) -> str:
    now = int(time.time())
    id = str(ULID())
    get_db().execute(
        'INSERT INTO recipes(id, title, content, tags, source_url, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, title, content, json.dumps(tags) if tags else None, source_url, now, now),
    )
    get_db().commit()
    return id


@bp.post('')
def create_recipe():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    content = (body.get('content') or '').strip()
    if not title or not content:
        return jsonify({'error': 'title and content required'}), 400
    id = _insert_recipe(title, content, body.get('tags'))
    return jsonify({'id': id}), 201


@bp.patch('/<id>')
def update_recipe(id):
    body = request.json or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'title' in body:
        updates['title'] = (body['title'] or '').strip()
    if 'content' in body:
        updates['content'] = body['content']
    if 'tags' in body:
        updates['tags'] = json.dumps(body['tags']) if body['tags'] else None
    if 'sourceUrl' in body:
        updates['source_url'] = body['sourceUrl']
    db = get_db()
    build_update(db, 'recipes', updates, 'id=?', (id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_recipe(id):
    db = get_db()
    db.execute('DELETE FROM recipes WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


def _strip_html(html: str) -> str:
    return strip_html(html, _MAX_PAGE_CHARS)


def _fetch_url_text(url: str) -> str:
    import requests
    resp = requests.get(
        url,
        timeout=15,
        headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Lunaschal/1.0'},
    )
    resp.raise_for_status()
    return _strip_html(resp.text)


@bp.post('/import')
def import_recipe():
    body = request.json or {}
    text = (body.get('text') or '').strip()
    url = (body.get('url') or '').strip()
    if bool(text) == bool(url):
        return jsonify({'error': 'provide exactly one of text or url'}), 400

    if url:
        if not url.startswith(('http://', 'https://')):
            return jsonify({'error': 'invalid url'}), 400
        try:
            text = _fetch_url_text(url)
        except Exception as e:
            return jsonify({'error': f'Could not fetch the page: {e}'}), 422

    parsed = parse_recipe(text)
    if not parsed:
        return jsonify({'error': 'Could not extract a recipe from the provided content'}), 422

    id = _insert_recipe(parsed['title'], parsed['content'], parsed.get('tags'), url or None)
    row = get_db().execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    return jsonify({'id': id, 'recipe': row_to_dict(row)}), 201
