import json
import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.ai.recipes import generate_recipe, parse_recipe
from backend.cookbook import storage
from backend.htmltext import strip_html
from backend.db.connection import build_update, get_db, row_to_dict, search_recipes_fts
from backend.imaging import HEIC_EXTS, transcode_to_jpeg
from backend.tags import tag_counts

bp = Blueprint('cookbook', __name__, url_prefix='/api/cookbook')

_MAX_PAGE_CHARS = 15000


def _media_url(media_id: str) -> str:
    return f'/api/cookbook/media/{media_id}'


def _recipe_media(db, recipe_id: str) -> list[dict]:
    rows = db.execute(
        'SELECT id, kind, position FROM recipe_media WHERE recipe_id=? ORDER BY position ASC, created_at ASC',
        (recipe_id,),
    ).fetchall()
    return [
        {'id': r['id'], 'kind': r['kind'], 'position': r['position'], 'url': _media_url(r['id'])}
        for r in rows
    ]


def _recipe_dict(db, row) -> dict:
    d = row_to_dict(row)
    d['media'] = _recipe_media(db, row['id'])
    return d


def _next_media_position(db, recipe_id: str) -> int:
    row = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS n FROM recipe_media WHERE recipe_id=?', (recipe_id,)
    ).fetchone()
    return row['n']


def _save_media_file(recipe_id: str, file, position: int):
    """Persist one upload, same contract as backend/routes/food.py's helper of
    the same name: HEIC/HEIF is transcoded to JPEG so it renders everywhere.
    Returns the public dict, or None if the type isn't allowed."""
    ext = storage.resolve_ext(file.mimetype, file.filename)
    if ext is None:
        return None
    is_heic = ext in HEIC_EXTS
    mime = file.mimetype
    if is_heic:
        ext, mime = 'jpg', 'image/jpeg'

    media_id = str(ULID())
    path = storage.media_path(recipe_id, media_id, ext)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)

    if is_heic:
        if not transcode_to_jpeg(file, path):
            return None
    else:
        file.save(path)

    kind = storage.kind_for_ext(ext)
    now = int(time.time())
    get_db().execute(
        'INSERT INTO recipe_media(id, recipe_id, kind, path, mime, position, created_at) VALUES (?,?,?,?,?,?,?)',
        (media_id, recipe_id, kind, str(path), mime, position, now),
    )
    return {'id': media_id, 'kind': kind, 'position': position, 'url': _media_url(media_id)}


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
    return jsonify([_recipe_dict(db, r) for r in rows])


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
    dicts = sorted([_recipe_dict(db, r) for r in rows], key=lambda d: id_rank.get(d['id'], 0))
    return jsonify(dicts)


@bp.get('/tags')
def list_tags():
    rows = get_db().execute('SELECT tags FROM recipes WHERE tags IS NOT NULL').fetchall()
    return jsonify(tag_counts(rows))


@bp.get('/<id>')
def get_recipe(id):
    db = get_db()
    row = db.execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_recipe_dict(db, row))


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
    # Multipart (text + media) or JSON (text only) — same split food.py's
    # create_entry makes, for the same reason: a dictated recipe with a photo
    # needs one request, and plain JSON stays for the import/programmatic path.
    if request.content_type and 'multipart/form-data' in request.content_type:
        form = request.form
        files = request.files.getlist('media')
        tags = _parse_tags_field(form.get('tags'))
    else:
        form = request.get_json(silent=True) or {}
        files = []
        tags = form.get('tags')

    title = (form.get('title') or '').strip()
    content = (form.get('content') or '').strip()
    if not title or not content:
        return jsonify({'error': 'title and content required'}), 400

    id = _insert_recipe(title, content, tags)
    for i, f in enumerate(files):
        if f and f.filename:
            _save_media_file(id, f, i)
    get_db().commit()

    row = get_db().execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    return jsonify(_recipe_dict(get_db(), row)), 201


def _parse_tags_field(raw) -> list | None:
    """Accept a JSON array string or a comma-separated string from a form field."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return [t for t in (s.strip() for s in raw.split(',')) if t]


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
    db.execute('DELETE FROM recipes WHERE id=?', (id,))  # cascades recipe_media rows
    db.commit()
    storage.delete_recipe_dir(id)
    return jsonify({'success': True})


# --- Media ---

@bp.post('/<id>/media')
def add_media(id):
    db = get_db()
    if not db.execute('SELECT 1 FROM recipes WHERE id=?', (id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    files = request.files.getlist('media')
    saved = []
    pos = _next_media_position(db, id)
    for f in files:
        if f and f.filename:
            res = _save_media_file(id, f, pos)
            if res:
                saved.append(res)
                pos += 1
    db.execute('UPDATE recipes SET updated_at=? WHERE id=?', (int(time.time()), id))
    db.commit()
    return jsonify({'media': saved}), 201


@bp.delete('/media/<media_id>')
def delete_media(media_id):
    db = get_db()
    row = db.execute('SELECT recipe_id, path FROM recipe_media WHERE id=?', (media_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM recipe_media WHERE id=?', (media_id,))
    db.execute('UPDATE recipes SET updated_at=? WHERE id=?', (int(time.time()), row['recipe_id']))
    db.commit()
    path = storage.resolve_stored_path(row['path'])
    if path is not None and path.is_file():
        path.unlink(missing_ok=True)
    return jsonify({'success': True})


@bp.get('/media/<media_id>')
def serve_media(media_id):
    row = get_db().execute('SELECT path, mime FROM recipe_media WHERE id=?', (media_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype=row['mime'] or None, conditional=True)


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
    db = get_db()
    row = db.execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    return jsonify({'id': id, 'recipe': _recipe_dict(db, row)}), 201


@bp.post('/generate')
def generate():
    body = request.json or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'prompt required'}), 400

    generated = generate_recipe(prompt)
    if not generated:
        return jsonify({'error': 'Could not generate a recipe from that description'}), 422

    id = _insert_recipe(generated['title'], generated['content'], generated.get('tags'))
    db = get_db()
    row = db.execute('SELECT * FROM recipes WHERE id=?', (id,)).fetchone()
    return jsonify({'id': id, 'recipe': _recipe_dict(db, row)}), 201
