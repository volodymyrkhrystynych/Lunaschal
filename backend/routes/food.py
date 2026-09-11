import json
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.ai import jobs
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
        'SELECT id, kind, position, transcript, transcript_status, transcript_error,'
        ' description, description_status, description_error'
        ' FROM food_media WHERE entry_id=? ORDER BY position ASC, created_at ASC',
        (entry_id,),
    ).fetchall()
    return [_media_dict(r) for r in rows]


def _media_dict(r) -> dict:
    d = {
        'id': r['id'],
        'kind': r['kind'],
        'position': r['position'],
        'url': _media_url(r['id']),
    }
    # Only for a clip: a photo carrying transcript fields would render an empty
    # "Transcript" block under every picture.
    if r['kind'] == 'audio':
        d['transcript'] = r['transcript']
        d['transcriptStatus'] = r['transcript_status']
        d['transcriptError'] = r['transcript_error']
    if r['kind'] == 'image':
        d['description'] = r['description']
        d['descriptionStatus'] = r['description_status']
        d['descriptionError'] = r['description_error']
    return d


def _linked_recipe(db, recipe_id: str | None) -> dict | None:
    if not recipe_id:
        return None
    row = db.execute('SELECT id, title FROM recipes WHERE id=?', (recipe_id,)).fetchone()
    return {'id': row['id'], 'title': row['title']} if row else None


def _entry_dict(db, row) -> dict:
    d = row_to_dict(row)
    d.pop('generatedNotes', None)
    d['polishing'] = db.execute(
        "SELECT 1 FROM llm_jobs WHERE target_id=? AND kind='food.structure'"
        " AND status IN ('pending', 'running')", (row['id'],),
    ).fetchone() is not None
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


def _save_media_file(
    entry_id: str, file, position: int, media_id: str | None = None,
    *, kind: str | None = None,
):
    """Persist one upload. Returns (public_dict, disk_path, kind), or None if the
    type isn't allowed. The public dict is what the client sees; disk_path/kind
    are for server-side use (EXIF). HEIC/HEIF is transcoded to JPEG so it renders
    everywhere; its EXIF (capture date + GPS) is carried across.

    `media_id` is supplied by the client for a photo that was queued offline, so
    the upload can be replayed: a second attempt finds the row already there and
    stops, rather than writing the same picture twice under a new id. Without
    that, a retry after a dropped response — the most likely way an upload
    fails — is exactly what produces the duplicate.
    """
    if media_id and get_db().execute(
        'SELECT 1 FROM food_media WHERE id=?', (media_id,)
    ).fetchone():
        return None
    ext = storage.resolve_ext(file.mimetype, file.filename)
    if ext is None:
        return None
    is_heic = ext in HEIC_EXTS
    mime = file.mimetype
    if is_heic:
        ext, mime = 'jpg', 'image/jpeg'

    media_id = media_id or str(ULID())
    path = storage.media_path(entry_id, media_id, ext)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)

    if is_heic:
        if not transcode_to_jpeg(file, path):
            return None
    else:
        file.save(path)

    # An explicit kind beats the extension. The recordings route below knows it
    # is holding a voice memo, and `recording.webm` on its own does not say so:
    # webm carries either, and an ambiguous container is read as video.
    kind = kind or storage.kind_for_ext(ext)
    now = int(time.time())
    get_db().execute(
        'INSERT OR IGNORE INTO food_media(id, entry_id, kind, path, mime, position, created_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (media_id, entry_id, kind, str(path), mime, position, now),
    )
    from backend.ai.images import is_vision_configured
    if kind == 'image' and is_vision_configured():
        _queue_description(media_id)
    public = {'id': media_id, 'kind': kind, 'position': position, 'url': _media_url(media_id)}
    return public, path, kind


def _parse_media_ids(raw) -> list:
    """The client's ids for the photos in this upload, positionally. Anything
    unparseable means "no ids" rather than an error: the ids are an idempotency
    hint, and refusing the meal over a malformed one would lose the capture."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


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

def structure_food_entry(entry_id: str, text: str, *, force_notes: bool = False) -> bool:
    """Structure the latest raw text with meal photos and standing memory.

    Metadata fills empty fields. Notes can refresh the previous generated
    version, so later clips/photos improve it; manual notes require explicit
    Polish. Never overwrite an edit made while inference was running.

    Always finishes by checking for a homemade/existing-recipe match — even
    when parsing found nothing new to fill in, since that's independent of
    whether this text described a *new* recipe."""
    from backend.memory import get_memory

    db = get_db()
    before = db.execute('SELECT * FROM food_entries WHERE id=?', (entry_id,)).fetchone()
    if not before:
        return False
    # A queued payload may predate another clip. Always polish the whole meal.
    text = before['raw_content'] or ''
    descriptions = db.execute(
        "SELECT description FROM food_media WHERE entry_id=? AND kind='image'"
        " AND description_status='done' AND description IS NOT NULL"
        ' ORDER BY position, created_at, id', (entry_id,),
    ).fetchall()
    context = '\n'.join(r['description'] for r in descriptions)[:15000]
    parsed = parse_food_entry(text, memory=get_memory(), descriptions=context)
    row = db.execute('SELECT * FROM food_entries WHERE id=?', (entry_id,)).fetchone()
    if not row:
        return False
    if row['raw_content'] != before['raw_content']:
        jobs.enqueue('food.structure', entry_id, {'text': row['raw_content'] or ''})
        return False

    notes_updated = False
    if parsed:
        updates: dict = {}
        for col in ('dish', 'place'):
            if not row[col] and parsed.get(col):
                updates[col] = parsed[col]
        if (parsed.get('notes') and row['notes'] == before['notes']
                and row['generated_notes'] == before['generated_notes']
                and (force_notes or not row['notes'] or
                     row['notes'] == row['generated_notes'])):
            updates['notes'] = parsed['notes']
            updates['generated_notes'] = parsed['notes']
            notes_updated = True
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
    return notes_updated


def _queue_description(media_id: str) -> None:
    db = get_db()
    db.execute("UPDATE food_media SET description_status='running',"
               ' description_error=NULL WHERE id=?', (media_id,))
    jobs.enqueue('food.describe_media', media_id, commit=False)


def describe_food_media(media_id: str) -> None:
    from backend.ai.images import describe_image
    from backend.ai.service import InferencePaused, Preempted

    db = get_db()
    row = db.execute('SELECT * FROM food_media WHERE id=?', (media_id,)).fetchone()
    if not row or row['kind'] != 'image':
        return
    described = False
    try:
        path = storage.resolve_stored_path(row['path'])
        if path is None:
            raise ValueError('The image file is missing')
        description = describe_image(
            path,
            system=(
                'Describe this meal photo as reference for correcting a food voice transcript. '
                'Name visible dishes and ingredients only as specifically as the image supports. '
                'Quote legible menu, packaging, brand, and restaurant text exactly, preserving '
                'spelling. State uncertainty and unreadable text instead of guessing. '
                'Do not infer hidden ingredients, recipes, taste, or what the person said. '
                'Return only a concise factual description.'
            ),
            prompt='Describe the food and quote any legible food-related names.',
            max_tokens=500,
        )
        db.execute("UPDATE food_media SET description=?, description_status='done',"
                   ' description_error=NULL WHERE id=?', (description, media_id))
        described = True
    except (InferencePaused, Preempted):
        raise
    except Exception as e:
        db.execute("UPDATE food_media SET description_status='error', description_error=?"
                   ' WHERE id=?', (str(e) or 'Description failed', media_id))
    entry = db.execute('SELECT raw_content FROM food_entries WHERE id=?',
                       (row['entry_id'],)).fetchone()
    if described and entry and entry['raw_content']:
        jobs.enqueue('food.structure', row['entry_id'],
                     {'text': entry['raw_content']}, commit=False)
    db.commit()


@bp.post('/media/<media_id>/describe')
def describe_media(media_id):
    db = get_db()
    row = db.execute('SELECT kind FROM food_media WHERE id=?', (media_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['kind'] != 'image':
        return jsonify({'error': 'Only photos can be described'}), 400
    _queue_description(media_id)
    db.commit()
    return jsonify({'success': True}), 202


@bp.post('/<id>/polish')
def polish_entry(id):
    row = get_db().execute('SELECT raw_content FROM food_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if not row['raw_content']:
        return jsonify({'error': 'No original transcript to polish'}), 400
    if not structure_food_entry(id, row['raw_content'], force_notes=True):
        return jsonify({'error': 'Food polishing unavailable; notes were kept'}), 503
    return jsonify({'success': True})


# --- Meal recordings -----------------------------------------------------------
#
# A clip spoken over the plate. It mirrors POST /api/journal/recordings beat for
# beat, because the contract is the same one: the phone holds the audio until
# the server confirms it and re-POSTs on every reconnect, so both ids come from
# the client and a replay must be a no-op — checked before the file is read, so
# a retry does not stream the recording to disk again to discover it was
# already there.
#
# The entry is created if it is not there yet. A clip can outrun the meal it
# belongs to (the composer sends the create first, but a boot sweep after a
# crash sends only the clip), and the alternative to INSERT OR IGNORE is a 404
# that strands the audio.

def _transcribe_media_bg(media_id: str, entry_id: str, path: str, *, now: bool = False) -> None:
    """Transcribe one meal clip, then fold it into the entry's raw text.

    The transcript is appended rather than assigned, and only the first time
    this clip produces one: several clips can be staged against one meal, and a
    re-run must refresh the clip's own text without pasting it into the entry
    twice — the same rule the journal's attachments follow.
    """
    def _run():
        from backend.routes import stt as stt_routes

        try:
            p = storage.resolve_stored_path(path)
            if p is None or not p.is_file():
                raise RuntimeError('The recording is missing')
            text = stt_routes.transcribe_file(p)
            status, error = 'done', None
        except Exception as e:
            text, status, error = None, 'error', str(e) or 'Failed'
            print(f'Meal clip transcription failed for {media_id}: {e}')

        try:
            db = get_db()
            prior = db.execute(
                'SELECT transcript FROM food_media WHERE id=?', (media_id,)
            ).fetchone()
            first_time = not (prior and prior['transcript'])
            updates = {'transcript_status': status, 'transcript_error': error}
            if text is not None:
                updates['transcript'] = text
            build_update(db, 'food_media', updates, 'id=?', (media_id,))
            if text is not None and first_time:
                _append_entry_text(db, entry_id, text)
            db.commit()
        except Exception as e:
            print(f'Failed to record meal transcription for {media_id}: {e}')
            return

        # Structure whatever the entry now says — including on the failure path,
        # where the meal may still have been typed or photographed. Re-running
        # per clip rather than waiting for the last one, for the reason the
        # journal does the same: it is not knowable here that another clip is
        # coming, and the shared FIFO worker means the version enqueued from the
        # last clip is the one that writes last.
        row = get_db().execute(
            'SELECT raw_content FROM food_entries WHERE id=?', (entry_id,)
        ).fetchone()
        body = (row['raw_content'] or '').strip() if row else ''
        if body:
            jobs.enqueue('food.structure', entry_id, {'text': body})

    if now:
        _run()
    else:
        jobs.enqueue('food.transcribe_media', media_id,
                     {'entry_id': entry_id, 'path': path})


def _append_entry_text(db, entry_id: str, text: str) -> None:
    """Add a clip's transcript to the end of a meal's raw note.

    A blank line between clips, not a space — the gap is the pause that was
    taken, and it is the only thing distinguishing "one thought, said twice"
    from a run-on sentence.
    """
    row = db.execute(
        'SELECT raw_content FROM food_entries WHERE id=?', (entry_id,)
    ).fetchone()
    if row is None:
        return
    existing = (row['raw_content'] or '').strip()
    merged = f'{existing}\n\n{text}' if existing else text
    db.execute(
        'UPDATE food_entries SET raw_content=?, updated_at=? WHERE id=?',
        (merged, int(time.time()), entry_id),
    )


@bp.post('/recordings')
def create_recording():
    audio = request.files.get('audio')
    if audio is None or not audio.filename:
        return jsonify({'error': 'Missing audio file'}), 400
    entry_id = (request.form.get('id') or '').strip()
    media_id = (request.form.get('mediaId') or '').strip()
    if not entry_id or not media_id:
        return jsonify({'error': 'id and mediaId are required'}), 400

    db = get_db()
    # Before the file is read: a replay must not stream the recording to disk
    # again only to discover the row is already there.
    existing = db.execute(
        'SELECT id, kind, position, transcript, transcript_status, transcript_error'
        ' FROM food_media WHERE id=?',
        (media_id,),
    ).fetchone()
    if existing:
        return jsonify({'id': entry_id, 'media': _media_dict(existing)}), 201

    now = int(time.time())
    db.execute(
        'INSERT OR IGNORE INTO food_entries(id, raw_content, created_at, updated_at)'
        ' VALUES (?,?,?,?)',
        (entry_id, None, now, now),
    )

    try:
        position = int(request.form.get('position'))
    except (TypeError, ValueError):
        position = _next_media_position(db, entry_id)

    res = _save_media_file(entry_id, audio, position, media_id, kind='audio')
    if res is None:
        # Nothing was written, so the entry this request may have just created
        # would be an empty meal nobody asked for. Only roll it back if it is
        # still empty — a replay arriving beside a real meal must not delete it.
        db.execute(
            'DELETE FROM food_entries WHERE id=? AND raw_content IS NULL'
            " AND dish IS NULL AND notes IS NULL"
            ' AND NOT EXISTS (SELECT 1 FROM food_media WHERE entry_id=?)',
            (entry_id, entry_id),
        )
        db.commit()
        return jsonify({'error': 'Unsupported audio type'}), 400

    db.execute(
        "UPDATE food_media SET transcript_status='running' WHERE id=?", (media_id,)
    )
    db.execute('UPDATE food_entries SET updated_at=? WHERE id=?', (now, entry_id))
    db.commit()
    _transcribe_media_bg(media_id, entry_id, str(res[1]))

    row = db.execute(
        'SELECT id, kind, position, transcript, transcript_status, transcript_error'
        ' FROM food_media WHERE id=?',
        (media_id,),
    ).fetchone()
    return jsonify({'id': entry_id, 'media': _media_dict(row)}), 201


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

    # Clips are not in this request — they go up one at a time through
    # /recordings, and may not have landed yet. The count is what says an
    # otherwise-empty create is a meal that was spoken rather than a mistake;
    # without it a capture that was only talked over is refused, and the
    # location and capture time it carries go with it.
    try:
        pending_clips = int(form.get('pendingClips') or 0)
    except (TypeError, ValueError):
        pending_clips = 0

    if not text and not files and not dish and not notes and pending_clips <= 0:
        return jsonify({'error': 'provide text, media, or details'}), 400

    now = int(time.time())
    # Client-supplied ULID so a meal captured offline replays idempotently —
    # and, unlike the text-only features, so its photos can be uploaded under
    # the same entry afterwards (POST /<id>/media) whichever order they land in.
    entry_id = (form.get('id') or '').strip() or str(ULID())
    db = get_db()
    cur = db.execute(
        'INSERT OR IGNORE INTO food_entries(id, raw_content, dish, place, notes, rating, tags, '
        'latitude, longitude, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (entry_id, text or None, dish, place, notes, rating, tags_json(tags) if tags else None,
         latitude, longitude, now, now),
    )
    if cur.rowcount == 0 and text:
        # A clip from the same capture can get here first — the meal's id is
        # minted at the first chunk, so /recordings may already have opened this
        # row, empty. A plain INSERT OR IGNORE would then drop what was typed
        # beside the audio. Only a row that is still empty is filled in; a real
        # replay finds text and is left alone.
        db.execute(
            'UPDATE food_entries SET raw_content=?, updated_at=? WHERE id=?'
            " AND COALESCE(raw_content, '')=''",
            (text, now, entry_id),
        )

    # Ids for the photos, positionally, when the client minted them (an offline
    # capture does). Sent as a JSON array so one field covers any number of them.
    media_ids = _parse_media_ids(form.get('mediaIds'))

    image_paths = []
    for i, f in enumerate(files):
        if f and f.filename:
            res = _save_media_file(entry_id, f, i, media_ids[i] if i < len(media_ids) else None)
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
        jobs.enqueue('food.structure', entry_id, {'text': text})
    elif dish:
        jobs.enqueue('food.recipe_match', entry_id)

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
        updates['generated_notes'] = None
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
