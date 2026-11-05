"""Purchases captured like meals: a receipt photo, a clip spoken over it, or both.

The same `spending_logs` rows the Daily page's typed lines write (those keep
their own three routes in backend/routes/lifestyle.py, which shipped phones
call). What is new here is the capture shape, and it is the food log's beat for
beat — client-minted ids for the entry and every piece of media so an offline
capture replays as a no-op, a clip allowed to arrive before its entry, EXIF
deciding when and where — so read backend/routes/food.py for the reasons; the
comments below are about where spending differs.

The difference that matters: a receipt arrives with no amount. The amount, the
merchant and the category come from the structuring pass reading the receipt
and the transcript, fill only what is still empty, and never overwrite a hand
edit. Until then the amount is NULL, and NULL is left out of every total rather
than counted as zero — a total that quietly omits a pending receipt is labelled
as such (`pendingCount`), one that counts it as $0 is just wrong.
"""
import json
import time
from datetime import date as date_cls, timedelta

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.ai import jobs
from backend.ai.spending import MAX_CENTS, parse_spending_entry
from backend.capture_time import optional_capture_time
from backend.day_boundary import day_key_for
from backend.db.connection import build_update, get_db, row_to_dict
from backend.food.exif import extract_photo_meta
from backend.geo import parse_coord
from backend.imaging import HEIC_EXTS, transcode_to_jpeg
from backend.spending import storage

bp = Blueprint('spending', __name__, url_prefix='/api/spending')

_MEDIA_COLS = ('id, kind, position, transcript, transcript_status, transcript_error,'
               ' description, description_status, description_error')

# How far back the chart may ask. A year of bars is already unreadable.
MAX_TREND_DAYS = 366


def _media_url(media_id: str) -> str:
    return f'/api/spending/media/{media_id}'


def _media_dict(r) -> dict:
    d = {'id': r['id'], 'kind': r['kind'], 'position': r['position'], 'url': _media_url(r['id'])}
    if r['kind'] == 'audio':
        d['transcript'] = r['transcript']
        d['transcriptStatus'] = r['transcript_status']
        d['transcriptError'] = r['transcript_error']
    if r['kind'] == 'image':
        d['description'] = r['description']
        d['descriptionStatus'] = r['description_status']
        d['descriptionError'] = r['description_error']
    return d


def _entry_media(db, entry_id: str) -> list[dict]:
    rows = db.execute(
        f'SELECT {_MEDIA_COLS} FROM spending_media WHERE entry_id=?'
        ' ORDER BY position ASC, created_at ASC', (entry_id,),
    ).fetchall()
    return [_media_dict(r) for r in rows]


def _entry_dict(db, row) -> dict:
    d = row_to_dict(row)
    d.pop('generatedNotes', None)
    # Still being read: a structuring pass is queued, or a receipt/clip is.
    d['reading'] = db.execute(
        "SELECT 1 FROM llm_jobs WHERE status IN ('pending', 'running') AND ("
        "  (kind='spending.structure' AND target_id=?)"
        "  OR (kind IN ('spending.describe_media', 'spending.transcribe_media')"
        "      AND target_id IN (SELECT id FROM spending_media WHERE entry_id=?)))",
        (row['id'], row['id']),
    ).fetchone() is not None
    d['media'] = _entry_media(db, row['id'])
    return d


def _parse_cents(raw):
    """(cents_or_None, error_or_None). Absent/blank is no amount, not an error:
    a receipt is allowed to arrive without one."""
    if raw is None or raw == '':
        return None, None
    if isinstance(raw, bool):
        return None, 'amountCents must be a whole number'
    if isinstance(raw, str):
        try:
            raw = int(raw.strip())
        except ValueError:
            return None, 'amountCents must be a whole number'
    if not isinstance(raw, int) or not 1 <= raw <= MAX_CENTS:
        return None, f'amountCents must be a whole number between 1 and {MAX_CENTS}'
    return raw, None


def _text(form, key, limit=200):
    v = form.get(key)
    if not isinstance(v, str):
        return None
    return v.strip()[:limit] or None


def _parse_media_ids(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


def _next_media_position(db, entry_id: str) -> int:
    return db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS n FROM spending_media WHERE entry_id=?',
        (entry_id,),
    ).fetchone()['n']


def _save_media_file(entry_id, file, position, media_id=None, *, kind=None):
    """Persist one upload: (public_dict, disk_path, kind), or None when it was a
    replay or not a type the ledger keeps. Photos and voice only — a video of a
    till is not a receipt, and nothing downstream could read one."""
    db = get_db()
    if media_id and db.execute('SELECT 1 FROM spending_media WHERE id=?', (media_id,)).fetchone():
        return None
    ext = storage.resolve_ext(file.mimetype, file.filename)
    if ext is None:
        return None
    kind = kind or storage.kind_for_ext(ext)
    if kind not in ('image', 'audio'):
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
    db.execute(
        'INSERT OR IGNORE INTO spending_media(id, entry_id, kind, path, mime, position, created_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (media_id, entry_id, kind, str(path), mime, position, int(time.time())),
    )
    from backend.ai.images import is_vision_configured
    if kind == 'image' and is_vision_configured():
        _queue_description(media_id)
    public = {'id': media_id, 'kind': kind, 'position': position, 'url': _media_url(media_id)}
    return public, path, kind


def _apply_photo_meta(db, entry_id: str, paths: list) -> None:
    """The receipt says when and where the money was spent, so its EXIF beats
    the upload time and the device's GPS — and moves the entry's day with it."""
    taken_at = latitude = longitude = None
    for p in paths:
        meta = extract_photo_meta(p)
        taken_at = taken_at or meta['taken_at']
        if latitude is None and meta['latitude'] is not None:
            latitude, longitude = meta['latitude'], meta['longitude']
    updates: dict = {}
    if taken_at:
        updates['created_at'] = taken_at
        updates['date'] = day_key_for(taken_at)
    if latitude is not None:
        updates['latitude'], updates['longitude'] = latitude, longitude
    if updates:
        build_update(db, 'spending_logs', updates, 'id=?', (entry_id,))


def _open_entry(db, entry_id: str, created_at: int) -> None:
    """Create the row a clip or photo belongs to, if it is not there yet."""
    db.execute(
        'INSERT OR IGNORE INTO spending_logs(id, date, created_at, updated_at) VALUES (?,?,?,?)',
        (entry_id, day_key_for(created_at), created_at, int(time.time())),
    )


def _capture_time(form) -> int:
    """The device's capture time, clamped to now; malformed falls back to now
    here because the clip or photo must not be lost over it."""
    now = int(time.time())
    try:
        captured = optional_capture_time(form)
    except ValueError:
        captured = None
    return min(captured, now) if captured is not None else now


# --- Background work ----------------------------------------------------------

def structure_spending_entry(entry_id: str) -> bool:
    """Fill the entry's empty fields from its note and its receipt readings.

    Returns whether anything was written. Merchant, category and amount are
    filled only while empty; the notes may refresh the previous generated
    version (later clips improve it) but never a hand edit. A note that grew
    while the model was running means this answer is stale — requeue, write
    nothing."""
    from backend.memory import get_memory

    db = get_db()
    before = db.execute('SELECT * FROM spending_logs WHERE id=?', (entry_id,)).fetchone()
    if not before:
        return False
    descriptions = db.execute(
        "SELECT description FROM spending_media WHERE entry_id=? AND kind='image'"
        " AND description_status='done' AND description IS NOT NULL"
        ' ORDER BY position, created_at, id', (entry_id,),
    ).fetchall()
    context = '\n\n'.join(r['description'] for r in descriptions)[:15000]
    parsed = parse_spending_entry(before['raw_content'] or '', memory=get_memory(),
                                  descriptions=context)
    row = db.execute('SELECT * FROM spending_logs WHERE id=?', (entry_id,)).fetchone()
    if not row or not parsed:
        return False
    if row['raw_content'] != before['raw_content']:
        jobs.enqueue('spending.structure', entry_id)
        return False

    updates: dict = {}
    for col, key in (('merchant', 'merchant'), ('category', 'category'), ('amount_cents', 'amountCents')):
        # Compared with `before` too: a hand edit that landed mid-call wins.
        if row[col] is None and before[col] is None and parsed.get(key) is not None:
            updates[col] = parsed[key]
    if (parsed.get('notes') and row['notes'] == before['notes']
            and row['generated_notes'] == before['generated_notes']
            and (not row['notes'] or row['notes'] == row['generated_notes'])):
        updates['notes'] = parsed['notes']
        updates['generated_notes'] = parsed['notes']
    if not updates:
        return False
    updates['updated_at'] = int(time.time())
    build_update(db, 'spending_logs', updates, 'id=?', (entry_id,))
    db.commit()
    return True


def _queue_description(media_id: str) -> None:
    get_db().execute("UPDATE spending_media SET description_status='running',"
                     ' description_error=NULL WHERE id=?', (media_id,))
    jobs.enqueue('spending.describe_media', media_id, commit=False)


def describe_spending_media(media_id: str) -> None:
    """Read a receipt photo into text the structuring pass can take a total from."""
    from backend.ai.images import describe_image
    from backend.ai.service import InferencePaused, Preempted

    db = get_db()
    row = db.execute('SELECT * FROM spending_media WHERE id=?', (media_id,)).fetchone()
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
                'Read this photo of a receipt (or a price tag, bill or screen showing a payment) '
                'as reference for a spending log. Quote exactly as printed: the merchant name, '
                'the date, each line item with its price, the subtotal, the taxes, and the TOTAL '
                'line, preserving spelling and digits. Say which lines are unreadable instead of '
                'guessing, and say plainly if the photo is not a receipt. Never compute a total '
                'that is not printed. Return only the transcription, no commentary.'
            ),
            prompt='Transcribe the merchant, the line items and the total on this receipt.',
            max_tokens=800,
        )
        db.execute("UPDATE spending_media SET description=?, description_status='done',"
                   ' description_error=NULL WHERE id=?', (description, media_id))
        described = True
    except (InferencePaused, Preempted):
        raise
    except Exception as e:
        db.execute("UPDATE spending_media SET description_status='error', description_error=?"
                   ' WHERE id=?', (str(e) or 'Reading failed', media_id))
    if described:
        jobs.enqueue('spending.structure', row['entry_id'], commit=False)
    db.commit()


def transcribe_spending_media(media_id: str) -> None:
    """Transcribe one clip, append it to the entry's note the first time, and
    structure whatever the entry now says — on failure too, since a receipt may
    still be there to read."""
    from backend.routes import stt as stt_routes

    db = get_db()
    row = db.execute('SELECT entry_id, path, transcript FROM spending_media WHERE id=?',
                     (media_id,)).fetchone()
    if not row:
        return
    try:
        p = storage.resolve_stored_path(row['path'])
        if p is None or not p.is_file():
            raise RuntimeError('The recording is missing')
        text = stt_routes.transcribe_file(p)
        status, error = 'done', None
    except Exception as e:
        text, status, error = None, 'error', str(e) or 'Failed'
    updates = {'transcript_status': status, 'transcript_error': error}
    if text is not None:
        updates['transcript'] = text
    build_update(db, 'spending_media', updates, 'id=?', (media_id,))
    if text and not row['transcript']:
        entry = db.execute('SELECT raw_content FROM spending_logs WHERE id=?',
                           (row['entry_id'],)).fetchone()
        if entry is not None:
            existing = (entry['raw_content'] or '').strip()
            db.execute('UPDATE spending_logs SET raw_content=?, updated_at=? WHERE id=?',
                       (f'{existing}\n\n{text}' if existing else text, int(time.time()),
                        row['entry_id']))
    jobs.enqueue('spending.structure', row['entry_id'], commit=False)
    db.commit()


# --- Entries ------------------------------------------------------------------

@bp.post('')
def create_entry():
    # Multipart (note + receipt photos) or JSON (note only).
    if request.content_type and 'multipart/form-data' in request.content_type:
        form, files = request.form, request.files.getlist('media')
    else:
        form, files = (request.get_json(silent=True) or {}), []

    text = _text(form, 'text', limit=15000)
    category = _text(form, 'category')
    merchant = _text(form, 'merchant')
    notes = _text(form, 'notes', limit=15000)
    cents, err = _parse_cents(form.get('amountCents'))
    if err:
        return jsonify({'error': err}), 400
    try:
        pending_clips = int(form.get('pendingClips') or 0)
    except (TypeError, ValueError):
        pending_clips = 0
    if not (text or files or category or merchant or notes or cents) and pending_clips <= 0:
        return jsonify({'error': 'provide a receipt, a recording, or details'}), 400
    try:
        captured_at = optional_capture_time(form)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    now = int(time.time())
    created_at = min(captured_at, now) if captured_at is not None else now
    entry_id = (form.get('id') or '').strip() or str(ULID())
    try:
        if str(ULID.from_str(entry_id)) != entry_id:
            raise ValueError
    except ValueError:
        return jsonify({'error': 'id must be a ULID'}), 400

    db = get_db()
    cur = db.execute(
        'INSERT OR IGNORE INTO spending_logs(id, date, category, amount_cents, merchant, raw_content,'
        ' notes, latitude, longitude, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (entry_id, day_key_for(created_at), category, cents, merchant, text, notes,
         parse_coord(form.get('latitude')), parse_coord(form.get('longitude')), created_at, now),
    )
    if cur.rowcount == 0 and text:
        # A clip of this capture got here first and opened the row empty.
        db.execute("UPDATE spending_logs SET raw_content=?, updated_at=? WHERE id=?"
                   " AND COALESCE(raw_content, '')=''", (text, now, entry_id))

    media_ids = _parse_media_ids(form.get('mediaIds'))
    image_paths = []
    for i, f in enumerate(files):
        if f and f.filename:
            res = _save_media_file(entry_id, f, i, media_ids[i] if i < len(media_ids) else None)
            if res and res[2] == 'image':
                image_paths.append(res[1])
    _apply_photo_meta(db, entry_id, image_paths)
    if text:
        jobs.enqueue('spending.structure', entry_id, commit=False)
    db.commit()
    row = db.execute('SELECT * FROM spending_logs WHERE id=?', (entry_id,)).fetchone()
    return jsonify(_entry_dict(db, row)), 201


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
    existing = db.execute(f'SELECT {_MEDIA_COLS} FROM spending_media WHERE id=?', (media_id,)).fetchone()
    if existing:
        return jsonify({'id': entry_id, 'media': _media_dict(existing)}), 201

    _open_entry(db, entry_id, _capture_time(request.form))
    try:
        position = int(request.form.get('position'))
    except (TypeError, ValueError):
        position = _next_media_position(db, entry_id)
    res = _save_media_file(entry_id, audio, position, media_id, kind='audio')
    if res is None:
        db.execute(
            'DELETE FROM spending_logs WHERE id=? AND raw_content IS NULL AND amount_cents IS NULL'
            ' AND category IS NULL AND merchant IS NULL AND notes IS NULL'
            ' AND NOT EXISTS (SELECT 1 FROM spending_media WHERE entry_id=?)', (entry_id, entry_id),
        )
        db.commit()
        return jsonify({'error': 'Unsupported audio type'}), 400
    db.execute("UPDATE spending_media SET transcript_status='running' WHERE id=?", (media_id,))
    db.execute('UPDATE spending_logs SET updated_at=? WHERE id=?', (int(time.time()), entry_id))
    jobs.enqueue('spending.transcribe_media', media_id, commit=False)
    db.commit()
    row = db.execute(f'SELECT {_MEDIA_COLS} FROM spending_media WHERE id=?', (media_id,)).fetchone()
    return jsonify({'id': entry_id, 'media': _media_dict(row)}), 201


@bp.get('/journal')
def journal_entries():
    """Every purchase shaped for the Journal feed, newest first, with media."""
    db = get_db()
    limit = min(int(request.args.get('limit', 100)), 200)
    rows = db.execute(
        'SELECT * FROM spending_logs ORDER BY created_at DESC, id DESC LIMIT ?', (limit,)
    ).fetchall()
    return jsonify([_entry_dict(db, r) for r in rows])


@bp.get('/trends')
def trends():
    """One row per 4am day, oldest first, ending today — every day of the window,
    so a day with no spending keeps its slot in the chart."""
    try:
        days = max(1, min(int(request.args.get('days', 30)), MAX_TREND_DAYS))
    except ValueError:
        return jsonify({'error': 'days must be a number'}), 400
    end = date_cls.fromisoformat(day_key_for())
    keys = [(end - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]
    series = {k: {'date': k, 'totalCents': 0, 'pendingCount': 0, 'byCategory': {}} for k in keys}
    for r in get_db().execute(
        'SELECT date, category, merchant, amount_cents FROM spending_logs WHERE date BETWEEN ? AND ?',
        (keys[0], keys[-1]),
    ):
        day = series.get(r['date'])
        if day is None:
            continue
        if r['amount_cents'] is None:
            day['pendingCount'] += 1
            continue
        day['totalCents'] += r['amount_cents']
        label = r['category'] or r['merchant'] or 'Other'
        day['byCategory'][label] = day['byCategory'].get(label, 0) + r['amount_cents']
    return jsonify([series[k] for k in keys])


@bp.get('/<entry_id>')
def get_entry(entry_id):
    db = get_db()
    row = db.execute('SELECT * FROM spending_logs WHERE id=?', (entry_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_entry_dict(db, row))


@bp.patch('/<entry_id>')
def update_entry(entry_id):
    body = request.get_json(silent=True) or {}
    db = get_db()
    if not db.execute('SELECT 1 FROM spending_logs WHERE id=?', (entry_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    updates: dict = {'updated_at': int(time.time())}
    for key in ('category', 'merchant'):
        if key in body:
            updates[key] = _text(body, key)
    if 'notes' in body:
        updates['notes'] = _text(body, 'notes', limit=15000)
        updates['generated_notes'] = None
    if 'amountCents' in body:
        cents, err = _parse_cents(body['amountCents'])
        if err:
            return jsonify({'error': err}), 400
        updates['amount_cents'] = cents
    build_update(db, 'spending_logs', updates, 'id=?', (entry_id,))
    db.commit()
    return jsonify(_entry_dict(db, db.execute('SELECT * FROM spending_logs WHERE id=?', (entry_id,)).fetchone()))


@bp.delete('/<entry_id>')
def delete_entry(entry_id):
    db = get_db()
    cur = db.execute('DELETE FROM spending_logs WHERE id=?', (entry_id,))
    db.execute('DELETE FROM spending_media WHERE entry_id=?', (entry_id,))
    db.commit()
    storage.delete_entry_dir(entry_id)
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


# --- Media --------------------------------------------------------------------

@bp.post('/<entry_id>/media')
def add_media(entry_id):
    db = get_db()
    if not db.execute('SELECT 1 FROM spending_logs WHERE id=?', (entry_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    media_ids = _parse_media_ids(request.form.get('mediaIds'))
    saved = []
    pos = _next_media_position(db, entry_id)
    for i, f in enumerate(request.files.getlist('media')):
        media_id = media_ids[i] if i < len(media_ids) else None
        existing = media_id and db.execute(
            f'SELECT {_MEDIA_COLS} FROM spending_media WHERE id=?', (media_id,)).fetchone()
        if existing:
            saved.append(_media_dict(existing))
        elif f and f.filename:
            res = _save_media_file(entry_id, f, pos, media_id)
            if res:
                saved.append(res[0])
                pos += 1
    db.execute('UPDATE spending_logs SET updated_at=? WHERE id=?', (int(time.time()), entry_id))
    db.commit()
    return jsonify({'id': entry_id, 'media': saved}), 201


@bp.get('/media/<media_id>')
def serve_media(media_id):
    row = get_db().execute('SELECT path, mime FROM spending_media WHERE id=?', (media_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype=row['mime'] or None, conditional=True)


@bp.delete('/media/<media_id>')
def delete_media(media_id):
    db = get_db()
    row = db.execute('SELECT entry_id, path FROM spending_media WHERE id=?', (media_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM spending_media WHERE id=?', (media_id,))
    db.execute('UPDATE spending_logs SET updated_at=? WHERE id=?', (int(time.time()), row['entry_id']))
    db.commit()
    path = storage.resolve_stored_path(row['path'])
    if path is not None and path.is_file():
        path.unlink(missing_ok=True)
    return jsonify({'success': True})
