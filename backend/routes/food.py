import json
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.ai.background import run_bg
from backend.ai.food import parse_food_entry
from backend.db.connection import build_update, get_db, row_to_dict
from backend.food import storage
from backend.food.exif import extract_photo_meta
from backend.food.recipe_match import check_homemade_recipe_match
from backend.geo import parse_coord
from backend.imaging import HEIC_EXTS, transcode_to_jpeg
from backend.routes.cookbook import _insert_recipe
from backend.tags import tag_counts, tags_json

bp = Blueprint('food', __name__, url_prefix='/api/food')


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _media_url(media_id: str) -> str:
    return f'/api/food/media/{media_id}'


def _entry_media(db, entry_id: str) -> list[dict]:
    rows = db.execute(
        'SELECT id, kind, position FROM food_media WHERE entry_id=? ORDER BY position ASC, created_at ASC',
        (entry_id,),
    ).fetchall()
    return [
        {'id': r['id'], 'kind': r['kind'], 'position': r['position'], 'url': _media_url(r['id'])}
        for r in rows
    ]


def _linked_recipe(db, recipe_id: str | None) -> dict | None:
    if not recipe_id:
        return None
    row = db.execute('SELECT id, title FROM recipes WHERE id=?', (recipe_id,)).fetchone()
    return {'id': row['id'], 'title': row['title']} if row else None


def _entry_dict(db, row) -> dict:
    d = row_to_dict(row)
    d['media'] = _entry_media(db, row['id'])
    d['recipe'] = _linked_recipe(db, row['recipe_id'])
    return d


def _parse_rating(raw) -> int | None:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None


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


# --- Media persistence ---


def _save_media_file(entry_id: str, file, position: int):
    """Persist one upload. Returns (public_dict, disk_path, kind), or None if the
    type isn't allowed. The public dict is what the client sees; disk_path/kind
    are for server-side use (EXIF). HEIC/HEIF is transcoded to JPEG so it renders
    everywhere; its EXIF (capture date + GPS) is carried across."""
    ext = storage.resolve_ext(file.mimetype, file.filename)
    if ext is None:
        return None
    is_heic = ext in HEIC_EXTS
    mime = file.mimetype
    if is_heic:
        ext, mime = 'jpg', 'image/jpeg'

    media_id = str(ULID())
    path = storage.media_path(entry_id, media_id, ext)
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
        'INSERT INTO food_media(id, entry_id, kind, path, mime, position, created_at) VALUES (?,?,?,?,?,?,?)',
        (media_id, entry_id, kind, str(path), mime, position, now),
    )
    public = {'id': media_id, 'kind': kind, 'position': position, 'url': _media_url(media_id)}
    return public, path, kind


def _photo_meta_from(paths: list) -> dict:
    """First capture date and first GPS fix found across the given image files.
    Both keys are None when no photo carried readable EXIF."""
    taken_at = None
    latitude = longitude = None
    for p in paths:
        meta = extract_photo_meta(p)
        if taken_at is None and meta['taken_at']:
            taken_at = meta['taken_at']
        if latitude is None and meta['latitude'] is not None:
            latitude, longitude = meta['latitude'], meta['longitude']
        if taken_at and latitude is not None:
            break
    return {'taken_at': taken_at, 'latitude': latitude, 'longitude': longitude}


def _next_media_position(db, entry_id: str) -> int:
    row = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS n FROM food_media WHERE entry_id=?', (entry_id,)
    ).fetchone()
    return row['n']


# --- Background structuring ---

def structure_food_entry(entry_id: str, text: str) -> None:
    """Fill empty fields on a food entry from its raw text via the LLM, and
    create+link a recipe if one was described. Only overwrites columns that are
    still empty, so manual input always wins. Safe to run in a worker thread.

    Always finishes by checking for a homemade/existing-recipe match — even
    when parsing found nothing new to fill in, since that's independent of
    whether this text described a *new* recipe."""
    parsed = parse_food_entry(text)
    db = get_db()
    row = db.execute('SELECT * FROM food_entries WHERE id=?', (entry_id,)).fetchone()
    if not row:
        return

    if parsed:
        updates: dict = {}
        for col in ('dish', 'place', 'notes'):
            if not row[col] and parsed.get(col):
                updates[col] = parsed[col]
        if row['rating'] is None and parsed.get('rating') is not None:
            updates['rating'] = parsed['rating']
        if not row['tags'] and parsed.get('tags'):
            updates['tags'] = tags_json(parsed['tags'])

        recipe = parsed.get('recipe')
        if recipe and row['recipe_id'] is None:
            updates['recipe_id'] = _insert_recipe(
                recipe['title'], recipe['content'], recipe.get('tags')
            )

        if updates:
            updates['updated_at'] = int(time.time())
            build_update(db, 'food_entries', updates, 'id=?', (entry_id,))
            db.commit()

    check_homemade_recipe_match(entry_id)


# --- Entries ---

@bp.get('')
def list_entries():
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    tag = request.args.get('tag', '').strip()
    db = get_db()
    if tag:
        rows = db.execute(
            'SELECT * FROM food_entries WHERE tags LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (f'%"{tag}"%', limit, offset),
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT * FROM food_entries ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (limit, offset),
        ).fetchall()
    return jsonify([_entry_dict(db, r) for r in rows])


@bp.get('/journal')
def journal_entries():
    """Food entries shaped for the Journal feed, newest first, each with media."""
    db = get_db()
    limit = min(int(request.args.get('limit', 100)), 200)
    rows = db.execute(
        'SELECT * FROM food_entries ORDER BY created_at DESC LIMIT ?', (limit,)
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            'id': r['id'],
            'dish': r['dish'],
            'place': r['place'],
            'rating': r['rating'],
            'notes': r['notes'],
            'latitude': r['latitude'],
            'longitude': r['longitude'],
            'createdAt': _iso(r['created_at']),
            'recipe': _linked_recipe(db, r['recipe_id']),
            'media': _entry_media(db, r['id']),
        })
    return jsonify(result)


@bp.get('/tags')
def list_tags():
    rows = get_db().execute('SELECT tags FROM food_entries WHERE tags IS NOT NULL').fetchall()
    return jsonify(tag_counts(rows))


@bp.get('/<id>')
def get_entry(id):
    db = get_db()
    row = db.execute('SELECT * FROM food_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_entry_dict(db, row))


@bp.post('')
def create_entry():
    # Multipart (text + media) or JSON (text only).
    if request.content_type and 'multipart/form-data' in request.content_type:
        form = request.form
        files = request.files.getlist('media')
    else:
        form = request.get_json(silent=True) or {}
        files = []

    text = (form.get('text') or '').strip()
    dish = (form.get('dish') or '').strip() or None
    place = (form.get('place') or '').strip() or None
    notes = (form.get('notes') or '').strip() or None
    rating = _parse_rating(form.get('rating'))
    tags = _parse_tags_field(form.get('tags'))
    latitude = parse_coord(form.get('latitude'))
    longitude = parse_coord(form.get('longitude'))

    if not text and not files and not dish and not notes:
        return jsonify({'error': 'provide text, media, or details'}), 400

    now = int(time.time())
    entry_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO food_entries(id, raw_content, dish, place, notes, rating, tags, '
        'latitude, longitude, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (entry_id, text or None, dish, place, notes, rating, tags_json(tags) if tags else None,
         latitude, longitude, now, now),
    )

    image_paths = []
    for i, f in enumerate(files):
        if f and f.filename:
            res = _save_media_file(entry_id, f, i)
            if res and res[2] == 'image':
                image_paths.append(res[1])

    # The photo is the source of truth for when/where the meal happened (the user
    # may attach an older photo). Its EXIF capture date and GPS override the
    # upload-time `now` and any device GPS the client sent.
    meta = _photo_meta_from(image_paths)
    overrides: dict = {}
    if meta['taken_at']:
        overrides['created_at'] = meta['taken_at']
    if meta['latitude'] is not None:
        overrides['latitude'] = meta['latitude']
        overrides['longitude'] = meta['longitude']
    if overrides:
        build_update(db, 'food_entries', overrides, 'id=?', (entry_id,))

    db.commit()

    # Structure the raw note in the background (fills empty fields, extracts a
    # recipe, and checks for a homemade/existing-recipe match). No-op when AI
    # is unconfigured or nothing new was parsed. When there's no text to
    # structure but a dish was given directly, still run the match check on
    # its own — it only needs dish/place/notes, not the raw note.
    if text:
        run_bg(lambda: structure_food_entry(entry_id, text))
    elif dish:
        run_bg(lambda: check_homemade_recipe_match(entry_id))

    row = db.execute('SELECT * FROM food_entries WHERE id=?', (entry_id,)).fetchone()
    return jsonify(_entry_dict(db, row)), 201


@bp.patch('/<id>')
def update_entry(id):
    body = request.get_json(silent=True) or {}
    db = get_db()
    if not db.execute('SELECT 1 FROM food_entries WHERE id=?', (id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    updates: dict = {'updated_at': int(time.time())}
    if 'dish' in body:
        updates['dish'] = (body['dish'] or '').strip() or None
    if 'place' in body:
        updates['place'] = (body['place'] or '').strip() or None
    if 'notes' in body:
        updates['notes'] = (body['notes'] or '').strip() or None
    if 'rating' in body:
        updates['rating'] = _parse_rating(body['rating'])
    if 'tags' in body:
        updates['tags'] = tags_json(body['tags'])
    if 'recipeId' in body:
        updates['recipe_id'] = body['recipeId'] or None
    build_update(db, 'food_entries', updates, 'id=?', (id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_entry(id):
    db = get_db()
    db.execute('DELETE FROM food_entries WHERE id=?', (id,))  # cascades food_media rows
    db.commit()
    storage.delete_entry_dir(id)
    return jsonify({'success': True})


# --- Media ---

@bp.post('/<id>/media')
def add_media(id):
    db = get_db()
    if not db.execute('SELECT 1 FROM food_entries WHERE id=?', (id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    files = request.files.getlist('media')
    saved = []
    pos = _next_media_position(db, id)
    for f in files:
        if f and f.filename:
            res = _save_media_file(id, f, pos)
            if res:
                saved.append(res[0])
                pos += 1
    db.execute('UPDATE food_entries SET updated_at=? WHERE id=?', (int(time.time()), id))
    db.commit()
    return jsonify({'media': saved}), 201


@bp.delete('/media/<media_id>')
def delete_media(media_id):
    db = get_db()
    row = db.execute('SELECT entry_id, path FROM food_media WHERE id=?', (media_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM food_media WHERE id=?', (media_id,))
    db.execute('UPDATE food_entries SET updated_at=? WHERE id=?', (int(time.time()), row['entry_id']))
    db.commit()
    path = storage.resolve_stored_path(row['path'])
    if path is not None and path.is_file():
        path.unlink(missing_ok=True)
    return jsonify({'success': True})


@bp.get('/media/<media_id>')
def serve_media(media_id):
    row = get_db().execute('SELECT path, mime FROM food_media WHERE id=?', (media_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype=row['mime'] or None, conditional=True)
