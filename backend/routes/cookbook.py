import json
import re
import threading
import time
from html.parser import HTMLParser

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.ai.embeddings import is_embeddings_configured
from backend.ai.rag import delete_recipe_embeddings, sync_recipe_embeddings
from backend.ai.recipes import parse_recipe
from backend.db.connection import get_db, row_to_dict, search_recipes_fts

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
    counts: dict[str, int] = {}
    for r in rows:
        try:
            for tag in json.loads(r['tags']):
                if isinstance(tag, str) and tag.strip():
                    counts[tag] = counts.get(tag, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
    return jsonify([
        {'name': name, 'count': count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ])


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
    _sync_embeddings_bg(id)
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
    set_clause = ', '.join(f'{k}=?' for k in updates)
    db = get_db()
    db.execute(f'UPDATE recipes SET {set_clause} WHERE id=?', [*updates.values(), id])
    db.commit()
    if 'title' in updates or 'content' in updates:
        _sync_embeddings_bg(id)
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_recipe(id):
    try:
        delete_recipe_embeddings(id)
    except Exception as e:
        print(f'Recipe embedding cleanup failed for {id}: {e}')
    db = get_db()
    db.execute('DELETE FROM recipes WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


class _TextExtractor(HTMLParser):
    _SKIP = {'script', 'style', 'noscript', 'svg', 'head'}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def _strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = '\n'.join(parser.parts)
    return re.sub(r'\n{3,}', '\n\n', text)[:_MAX_PAGE_CHARS]


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
    _sync_embeddings_bg(id)
    row = get_db().execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    return jsonify({'id': id, 'recipe': row_to_dict(row)}), 201


def _sync_embeddings_bg(recipe_id: str) -> None:
    def _sync():
        try:
            if is_embeddings_configured():
                sync_recipe_embeddings(recipe_id)
        except Exception as e:
            print(f'Embedding sync failed for recipe {recipe_id}: {e}')
    threading.Thread(target=_sync, daemon=True).start()
