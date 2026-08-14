import json
import queue
import re
import threading
import time
from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context
from ulid import ULID
from backend.db.connection import build_update, get_db, row_to_dict, search_journal_fts
from backend.day_boundary import day_bounds, day_key_for
from backend.ai.journal import (
    PolishUnavailable,
    polish_journal_entry,
    generate_journal_metadata,
)
from backend.ai.background import run_bg
from backend.journal import storage
from backend.tags import tags_json

bp = Blueprint('journal', __name__, url_prefix='/api/journal')

_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def _notify_subscribers(entry_id: str) -> None:
    with _subscribers_lock:
        for q in _subscribers:
            q.put(entry_id)


@bp.get('/events')
def events():
    q: queue.Queue = queue.Queue()
    with _subscribers_lock:
        _subscribers.append(q)

    def generate():
        try:
            while True:
                try:
                    entry_id = q.get(timeout=30)
                    yield f'data: {json.dumps({"id": entry_id})}\n\n'
                except queue.Empty:
                    yield ': heartbeat\n\n'
        finally:
            with _subscribers_lock:
                _subscribers.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _enrich_with_curated_tags(db, dicts: list[dict]) -> list[dict]:
    if not dicts:
        return dicts
    ids = [d['id'] for d in dicts]
    placeholders = ','.join('?' * len(ids))
    tag_rows = db.execute(
        f'SELECT jec.entry_id, ct.name'
        f' FROM journal_entry_curated_tags jec'
        f' JOIN curated_tags ct ON ct.id = jec.tag_id'
        f' WHERE jec.entry_id IN ({placeholders})',
        ids,
    ).fetchall()
    tag_map: dict[str, list[str]] = {}
    for tr in tag_rows:
        tag_map.setdefault(tr['entry_id'], []).append(tr['name'])
    for d in dicts:
        d['curatedTags'] = tag_map.get(d['id'], [])
    return dicts


def _enrich_with_fic_refs(db, dicts: list[dict]) -> list[dict]:
    if not dicts:
        return dicts
    ids = [d['id'] for d in dicts]
    placeholders = ','.join('?' * len(ids))
    ref_rows = db.execute(
        f'SELECT jefr.journal_entry_id, jefr.fic_id, f.title AS fic_title,'
        f' jefr.chapter_id, fc.title AS chapter_title'
        f' FROM journal_entry_fic_refs jefr'
        f' JOIN fics f ON f.id = jefr.fic_id'
        f' LEFT JOIN fic_chapters fc ON fc.id = jefr.chapter_id'
        f' WHERE jefr.journal_entry_id IN ({placeholders})',
        ids,
    ).fetchall()
    ref_map: dict[str, list[dict]] = {}
    for r in ref_rows:
        ref_map.setdefault(r['journal_entry_id'], []).append({
            'ficId': r['fic_id'],
            'ficTitle': r['fic_title'],
            'chapterId': r['chapter_id'],
            'chapterTitle': r['chapter_title'],
        })
    for d in dicts:
        d['ficRefs'] = ref_map.get(d['id'], [])
    return dicts


@bp.get('')
def list_entries():
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    curated_tag_id = request.args.get('curated_tag_id')
    db = get_db()
    if curated_tag_id:
        rows = db.execute(
            'SELECT je.* FROM journal_entries je'
            ' JOIN journal_entry_curated_tags jec ON je.id = jec.entry_id'
            ' WHERE jec.tag_id = ?'
            ' ORDER BY je.created_at DESC LIMIT ? OFFSET ?',
            (curated_tag_id, limit, offset),
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT * FROM journal_entries ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (limit, offset),
        ).fetchall()
    dicts = [row_to_dict(r) for r in rows]
    return jsonify(_enrich_with_attachments(
        db, _enrich_with_fic_refs(db, _enrich_with_curated_tags(db, dicts))
    ))


@bp.get('/search')
def search():
    query = request.args.get('query', '').strip()
    limit = min(int(request.args.get('limit', 50)), 100)
    if not query:
        return jsonify([])
    fts = search_journal_fts(query, limit)
    if not fts:
        return jsonify([])
    db = get_db()
    id_rank = {r['id']: r['rank'] for r in fts}
    placeholders = ','.join('?' * len(id_rank))
    rows = db.execute(
        f'SELECT * FROM journal_entries WHERE id IN ({placeholders})',
        list(id_rank),
    ).fetchall()
    dicts = sorted([row_to_dict(r) for r in rows], key=lambda d: id_rank.get(d['id'], 0))
    return jsonify(_enrich_with_attachments(
        db, _enrich_with_fic_refs(db, _enrich_with_curated_tags(db, dicts))
    ))


@bp.get('/<id>')
def get_entry(id):
    db = get_db()
    row = db.execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_enrich_with_attachments(db, [row_to_dict(row)])[0])


@bp.post('')
def create_entry():
    body = request.json or {}
    raw_content = body.get('raw_content', '').strip()
    content = body.get('content', '').strip()

    if raw_content:
        # STT path: save immediately with raw text, polish in background
        content = raw_content
    elif not content:
        return jsonify({'error': 'content required'}), 400
    else:
        raw_content = None

    title = body.get('title') or None
    tags = body.get('tags') or None

    now = int(time.time())
    # Accept a client-supplied ULID so an offline-queued create replays
    # idempotently: INSERT OR IGNORE makes a duplicate a no-op, and rowcount==0
    # means we've already saved this entry — skip the background work.
    id = body.get('id') or str(ULID())
    cur = get_db().execute(
        'INSERT OR IGNORE INTO journal_entries(id, content, raw_content, title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, content, raw_content, title, tags_json(tags), now, now),
    )
    get_db().commit()
    if cur.rowcount == 0:
        return jsonify({'id': id}), 201
    _notify_subscribers(id)
    if raw_content:
        _polish_bg(id, raw_content)
    if not title or not tags:
        _generate_metadata_bg(id, content)
    return jsonify({'id': id}), 201


def _attachment_polish_context(entry_id: str) -> str | None:
    """Non-speech descriptions of the entry's audio/video attachments, offered
    to the polish model as context for fixing a misheard word — the
    description comes from a different model/listener, and may have gotten a
    name right that raw_content's speech-to-text did not (see the 'Context:'
    handling in backend/ai/journal.py's system prompt)."""
    rows = get_db().execute(
        "SELECT name, description FROM journal_attachments"
        " WHERE entry_id=? AND kind IN ('audio','video')"
        " AND description_status='done' AND description IS NOT NULL",
        (entry_id,),
    ).fetchall()
    if not rows:
        return None
    return '\n'.join(f"{r['name']}: {r['description']}" for r in rows)


@bp.post('/<id>/polish')
def polish_entry(id):
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    source = row['raw_content'] or ''
    if not source.strip():
        return jsonify({'error': 'No original transcription to polish'}), 400
    try:
        polished = polish_journal_entry(source, context=_attachment_polish_context(id))
    except PolishUnavailable as e:
        # Leave `content` exactly as it is. Writing the raw transcript back here
        # is what used to make an offline llama-server look like a broken button.
        return jsonify({'error': f'Polish unavailable: {e}'}), 503
    db = get_db()
    db.execute(
        'UPDATE journal_entries SET content=?, updated_at=? WHERE id=?',
        (polished, int(time.time()), id),
    )
    db.commit()
    _notify_subscribers(id)
    # Regenerate title/tags from the polished text if they're missing
    entry = row_to_dict(db.execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone())
    if not entry.get('title') or not entry.get('tags'):
        _generate_metadata_bg(id, polished)
    return jsonify({'success': True, 'content': polished})


@bp.patch('/<id>')
def update_entry(id):
    body = request.json or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'content' in body:
        updates['content'] = body['content']
    if 'title' in body:
        updates['title'] = body['title']
    if 'tags' in body:
        updates['tags'] = tags_json(body['tags'])
    build_update(get_db(), 'journal_entries', updates, 'id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_entry(id):
    get_db().execute('DELETE FROM journal_entries WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


# --- Merging voice-only entries -----------------------------------------------
#
# A recording made with the bottom bar's Record button (create_recording_entry,
# below) lands as its own entry with no body text. If it turns out to belong
# with something already written that day, the entry as a whole is pointless —
# only merging is: fold its one attachment into another entry and delete the
# now-empty husk. Restricted to "nothing but a single recording" so a merge can
# never silently drop text or other attachments the source entry was carrying.

def _local_day(created_at: int) -> str:
    return day_key_for(created_at)


def _is_voice_only_entry(db, entry_id: str) -> bool:
    row = db.execute(
        'SELECT content FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    if row is None or (row['content'] or '').strip():
        return False
    attachments = db.execute(
        'SELECT kind FROM journal_attachments WHERE entry_id=?', (entry_id,)
    ).fetchall()
    return len(attachments) == 1 and attachments[0]['kind'] == 'audio'


@bp.get('/<id>/merge-candidates')
def merge_candidates(id):
    """Other entries from the same local day as `id`, for the merge picker —
    matches the day window backend/routes/calendar.py's related-journals
    uses, since journal timestamps are local unix seconds either way."""
    db = get_db()
    row = db.execute(
        'SELECT created_at FROM journal_entries WHERE id=?', (id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    day = _local_day(row['created_at'])
    start, end = day_bounds(day)
    rows = db.execute(
        'SELECT * FROM journal_entries WHERE created_at >= ? AND created_at < ? AND id != ?'
        ' ORDER BY created_at DESC',
        (start, end, id),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/<id>/merge')
def merge_entry(id):
    body = request.json or {}
    target_id = body.get('targetId')
    if not target_id:
        return jsonify({'error': 'targetId required'}), 400
    if target_id == id:
        return jsonify({'error': 'Cannot merge an entry into itself'}), 400

    db = get_db()
    source = db.execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not source:
        return jsonify({'error': 'Not found'}), 404
    target = db.execute(
        'SELECT * FROM journal_entries WHERE id=?', (target_id,)
    ).fetchone()
    if not target:
        return jsonify({'error': 'Target entry not found'}), 404

    if not _is_voice_only_entry(db, id):
        return jsonify({
            'error': 'Only an entry with nothing but a single recording can be merged into another entry',
        }), 400
    if _local_day(source['created_at']) != _local_day(target['created_at']):
        return jsonify({'error': 'Entries must be from the same day'}), 400

    attachment = db.execute(
        'SELECT id FROM journal_attachments WHERE entry_id=?', (id,)
    ).fetchone()
    next_position = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next FROM journal_attachments'
        ' WHERE entry_id=?',
        (target_id,),
    ).fetchone()['next']
    db.execute(
        'UPDATE journal_attachments SET entry_id=?, position=? WHERE id=?',
        (target_id, next_position, attachment['id']),
    )
    db.execute('DELETE FROM journal_entries WHERE id=?', (id,))
    db.commit()
    _notify_subscribers(target_id)

    merged = row_to_dict(
        db.execute('SELECT * FROM journal_entries WHERE id=?', (target_id,)).fetchone()
    )
    return jsonify(_enrich_with_attachments(
        db, _enrich_with_fic_refs(db, _enrich_with_curated_tags(db, [merged]))
    )[0])


def _polish_bg(journal_id: str, raw_content: str) -> None:
    def _run():
        try:
            polished = polish_journal_entry(
                raw_content, context=_attachment_polish_context(journal_id)
            )
            if polished == raw_content:
                return
        except PolishUnavailable as e:
            # The entry is already saved with its raw text as `content`; nothing
            # to undo, and nobody is waiting on a response.
            print(f'Background polish unavailable for {journal_id}: {e}')
            return
        try:
            db = get_db()
            db.execute(
                'UPDATE journal_entries SET content=?, updated_at=? WHERE id=?',
                (polished, int(time.time()), journal_id),
            )
            db.commit()
            _notify_subscribers(journal_id)
        except Exception as e:
            print(f'Background polish failed for {journal_id}: {e}')
    run_bg(_run)


# --- Attachments -------------------------------------------------------------
#
# Audio clips and photos hung off an entry. Two things shape the design:
#
# - Transcription and captioning are opt-in per attachment, never automatic on
#   upload. A voice memo attached to an entry is often kept *as audio* — the
#   point is to have the recording, not a wall of text — and on this hardware a
#   transcription is a real cost (Whisper/Parakeet on CPU) that shouldn't be
#   spent on every upload.
# - They run on the shared single-worker background executor and report through
#   `transcript_status`, so the request returns immediately and the existing
#   /events SSE stream tells the client when the text has landed.

# A phone voice memo of a long walk is tens of MB; a photo off the same phone is
# a few; a minute of 4K video is a few hundred. All three are generous ceilings
# whose job is to stop a mis-picked file from filling the disk, not to be a
# meaningful limit on real attachments. Uploads stream to disk rather than being
# read into memory, so a large video costs disk, not RAM.
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024

_MAX_BYTES_BY_KIND = {
    'audio': MAX_AUDIO_BYTES,
    'image': MAX_IMAGE_BYTES,
    'video': MAX_VIDEO_BYTES,
}

_UNSUPPORTED_TYPE = 'Unsupported file type — audio, video or images only'

_DEFAULT_NAMES = {'audio': 'Audio', 'video': 'Video', 'image': 'Photo'}

_ATTACHMENT_COLS = (
    'id, entry_id, kind, name, path, mime, size, position,'
    ' transcript, transcript_status, transcript_error,'
    ' description, description_status, description_error,'
    ' latitude, longitude, created_at'
)


def _attachment_dict(row) -> dict:
    d = row_to_dict(row)
    # `path` is a server-side filesystem location; the client gets a URL instead.
    d.pop('path', None)
    d['url'] = f'/api/journal/attachments/{row["id"]}/file'
    return d


def _enrich_with_attachments(db, dicts: list[dict]) -> list[dict]:
    if not dicts:
        return dicts
    ids = [d['id'] for d in dicts]
    placeholders = ','.join('?' * len(ids))
    rows = db.execute(
        f'SELECT {_ATTACHMENT_COLS} FROM journal_attachments'
        f' WHERE entry_id IN ({placeholders})'
        f' ORDER BY position, created_at',
        ids,
    ).fetchall()
    by_entry: dict[str, list[dict]] = {}
    for r in rows:
        by_entry.setdefault(r['entry_id'], []).append(_attachment_dict(r))
    for d in dicts:
        d['attachments'] = by_entry.get(d['id'], [])
    return dicts


def _load_attachment(attachment_id: str):
    return get_db().execute(
        f'SELECT {_ATTACHMENT_COLS} FROM journal_attachments WHERE id=?',
        (attachment_id,),
    ).fetchone()


@bp.get('/<id>/attachments')
def list_attachments(id):
    db = get_db()
    rows = db.execute(
        f'SELECT {_ATTACHMENT_COLS} FROM journal_attachments WHERE entry_id=?'
        f' ORDER BY position, created_at',
        (id,),
    ).fetchall()
    return jsonify([_attachment_dict(r) for r in rows])


def _store_attachment(
    entry_id: str, file, name: str | None, attachment_id: str | None = None
):
    """Save one uploaded file as an attachment of `entry_id`.

    Returns `(attachment_dict, None)` on success and `(None, (error, status))`
    on a rejected upload. Split out of the upload route so the one-shot
    recording route below can reuse it — the two differ only in where the entry
    comes from, not in how a file becomes an attachment.

    `attachment_id` lets a caller supply the id instead of minting one. Only the
    recording route does: a phone holds its audio until the upload is confirmed,
    so the same clip is re-POSTed until it lands, and a client-chosen id is what
    lets the replay be recognized (see `create_recording_entry`).
    """
    # NOT `or not file.filename`: a voice memo dragged out of the iOS Voice Memos
    # app arrives as a File with an empty name, and rejecting it here turned a
    # working drag-and-drop into "file is required". A nameless upload is fine —
    # the mime type carries the extension and _DEFAULT_NAMES carries the label.
    resolved = storage.resolve_upload(file.mimetype, file.filename)
    if resolved is None:
        return None, (_UNSUPPORTED_TYPE, 400)
    ext, kind = resolved

    # The user's label for the attachment. Falling back to the filename keeps a
    # list of attachments readable even when someone skips naming them.
    name = (name or '').strip()
    if not name:
        name = (file.filename or '').rsplit('/', 1)[-1] or _DEFAULT_NAMES[kind]

    attachment_id = attachment_id or str(ULID())
    path = storage.attachment_path(attachment_id, ext)
    if path is None:
        return None, (_UNSUPPORTED_TYPE, 400)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Streamed to disk rather than read() into memory: a phone video is happily
    # several hundred MB, and this app also runs on a handheld with 8 GB of RAM.
    file.save(path)

    size = path.stat().st_size
    if size == 0:
        storage.delete_attachment_dir(attachment_id)
        return None, ('file is empty', 400)
    if size > _MAX_BYTES_BY_KIND[kind]:
        storage.delete_attachment_dir(attachment_id)
        return None, ('file is too large', 413)

    # A photo should be located by where it was taken, not where it was
    # uploaded from — same helper the food log uses (backend/food/exif.py).
    latitude = longitude = None
    if kind == 'image':
        from backend.food.exif import extract_photo_meta
        meta = extract_photo_meta(path)
        latitude, longitude = meta['latitude'], meta['longitude']

    # Non-speech audio description auto-fires on upload (unlike the transcript,
    # which stays opt-in) as long as an audio model is actually configured —
    # otherwise every clip would land with a dead 'no model configured' error.
    from backend.ai.audio_description import is_audio_configured
    auto_describe = kind in ('audio', 'video') and is_audio_configured()
    description_status = 'running' if auto_describe else 'idle'

    db = get_db()
    position = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next FROM journal_attachments'
        ' WHERE entry_id=?',
        (entry_id,),
    ).fetchone()['next']
    try:
        # OR IGNORE, not plain INSERT: with a client-supplied id, two replays of
        # the same recording can race past the caller's existence check. Losing
        # that race must be a no-op — the `raise` below would rmtree the winner's
        # file out from under a perfectly good row.
        cur = db.execute(
            'INSERT OR IGNORE INTO journal_attachments'
            '(id, entry_id, kind, name, path, mime, size, position,'
            ' transcript_status, description_status, latitude, longitude, created_at)'
            " VALUES (?,?,?,?,?,?,?,?,'idle',?,?,?,?)",
            (attachment_id, entry_id, kind, name, str(path), file.mimetype or None,
             size, position, description_status, latitude, longitude, int(time.time())),
        )
        db.commit()
    except Exception:
        # Don't leave a file on disk that no row points at.
        storage.delete_attachment_dir(attachment_id)
        raise
    if cur.rowcount == 0:
        return _attachment_dict(_load_attachment(attachment_id)), None

    _notify_subscribers(entry_id)
    if auto_describe:
        _describe_attachment_bg(attachment_id, entry_id, str(path), name)
    return _attachment_dict(_load_attachment(attachment_id)), None


@bp.post('/<id>/attachments')
def upload_attachment(id):
    entry = get_db().execute(
        'SELECT id FROM journal_entries WHERE id=?', (id,)
    ).fetchone()
    if not entry:
        return jsonify({'error': 'Not found'}), 404

    file = request.files.get('file')
    if file is None:
        return jsonify({'error': 'file is required'}), 400

    attachment, failure = _store_attachment(id, file, request.form.get('name'))
    if failure is not None:
        message, status = failure
        return jsonify({'error': message}), status
    return jsonify(attachment), 201


_ULID_RE = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')


def _client_id(value: str | None) -> str | None:
    """A client-supplied ULID, or None if absent. Raises ValueError on a
    malformed one rather than falling back to a fresh id — an attachment id
    becomes a directory name, and silently substituting a different id would
    also break the very replay the client is counting on.
    """
    value = (value or '').strip()
    if not value:
        return None
    if not _ULID_RE.match(value):
        raise ValueError('id must be a ULID')
    return value


@bp.post('/recordings')
def create_recording_entry():
    """Save a recording as a journal entry *without* transcribing it.

    The bottom bar's Record button. The clip is the entry — there is no body
    text to write, so the row is created with an empty `content` and the audio
    hangs off it as an attachment. Nothing goes to speech-to-text and no
    metadata is generated: an empty body would only produce an invented title.
    The attachment's own Transcribe button is still there if the words are
    wanted later.

    Entry and attachment are created in one request so a failed upload can't
    leave an empty entry behind — the entry row is removed if the file is
    rejected.

    **Replaying this request must be a no-op.** The phone keeps a recording in
    IndexedDB until the server confirms it landed, and re-POSTs it on every
    reconnect until then — so a retry after a response that never made it back
    is the normal case, not an edge case. `id` and `attachmentId` therefore come
    from the client, and both halves are idempotent: `INSERT OR IGNORE` for the
    entry (as in `create_entry`), and an early return on an attachment id we
    already hold. The attachment check comes *first*, before the file is read,
    so a replay doesn't stream a hundred megabytes to disk to discover it was
    already there.
    """
    try:
        entry_id = _client_id(request.form.get('id'))
        attachment_id = _client_id(request.form.get('attachmentId'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    db = get_db()

    if attachment_id:
        existing = _load_attachment(attachment_id)
        if existing is not None:
            # Already stored. Answer as if we'd just saved it so the client
            # clears its local copy — the whole point of the retry loop.
            return jsonify(
                {'id': existing['entry_id'], 'attachment': _attachment_dict(existing)}
            ), 201

    file = request.files.get('file')
    if file is None:
        return jsonify({'error': 'file is required'}), 400

    now = int(time.time())
    entry_id = entry_id or str(ULID())
    cur = db.execute(
        'INSERT OR IGNORE INTO journal_entries(id, content, raw_content, title,'
        ' tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (entry_id, '', None, (request.form.get('title') or '').strip() or None,
         None, now, now),
    )
    db.commit()
    # Only an entry this request created may be cleaned up below: a replay whose
    # file is rejected must not delete the entry an earlier call got right.
    created_entry = cur.rowcount > 0

    def _rollback_entry():
        if not created_entry:
            return
        db.execute('DELETE FROM journal_entries WHERE id=?', (entry_id,))
        db.commit()

    try:
        attachment, failure = _store_attachment(
            entry_id, file, request.form.get('name') or 'Recording', attachment_id
        )
    except Exception:
        _rollback_entry()
        raise
    if failure is not None:
        _rollback_entry()
        message, status = failure
        return jsonify({'error': message}), status

    _notify_subscribers(entry_id)
    return jsonify({'id': entry_id, 'attachment': attachment}), 201


@bp.patch('/attachments/<attachment_id>')
def update_attachment(attachment_id):
    body = request.json or {}
    if 'name' not in body:
        return jsonify({'error': 'name required'}), 400
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name cannot be empty'}), 400
    db = get_db()
    cur = build_update(db, 'journal_attachments', {'name': name}, 'id=?', (attachment_id,))
    db.commit()
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    row = _load_attachment(attachment_id)
    _notify_subscribers(row['entry_id'])
    return jsonify(_attachment_dict(row))


@bp.delete('/attachments/<attachment_id>')
def delete_attachment(attachment_id):
    db = get_db()
    row = _load_attachment(attachment_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    entry_id = row['entry_id']
    db.execute('DELETE FROM journal_attachments WHERE id=?', (attachment_id,))
    db.commit()
    storage.delete_attachment_dir(attachment_id)
    _notify_subscribers(entry_id)
    return jsonify({'success': True})


@bp.get('/attachments/<attachment_id>/file')
def get_attachment_file(attachment_id):
    row = get_db().execute(
        'SELECT path, mime, name FROM journal_attachments WHERE id=?',
        (attachment_id,),
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    # conditional=True so <audio> range requests work — seeking in a long voice
    # memo otherwise re-downloads the whole file on every scrub.
    return send_file(path, mimetype=row['mime'] or None, conditional=True)


@bp.post('/attachments/<attachment_id>/transcribe')
def transcribe_attachment(attachment_id):
    """Queue transcription (audio/video) or captioning (image) for one attachment.

    Returns 202 immediately; the result arrives on the row and is pushed to the
    client through the /events stream.
    """
    db = get_db()
    row = _load_attachment(attachment_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['transcript_status'] == 'running':
        return jsonify({'error': 'Already running'}), 409

    db.execute(
        "UPDATE journal_attachments SET transcript_status='running', transcript_error=NULL"
        ' WHERE id=?',
        (attachment_id,),
    )
    db.commit()
    _notify_subscribers(row['entry_id'])
    _transcribe_attachment_bg(attachment_id, row['entry_id'], row['kind'],
                              row['path'], row['name'])
    return jsonify(_attachment_dict(_load_attachment(attachment_id))), 202


@bp.post('/attachments/<attachment_id>/describe-audio')
def describe_audio_attachment(attachment_id):
    """Queue non-speech audio description for an audio/video attachment.

    Separate from /transcribe: this asks a different, audio-capable model what
    is happening in the recording beyond the words said — see
    backend/ai/audio_description.py. Returns 202 immediately; the result
    arrives on the row and is pushed to the client through the /events stream.
    """
    db = get_db()
    row = _load_attachment(attachment_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['kind'] not in ('audio', 'video'):
        return jsonify({'error': 'Only audio and video attachments can be described'}), 400
    if row['description_status'] == 'running':
        return jsonify({'error': 'Already running'}), 409

    db.execute(
        "UPDATE journal_attachments SET description_status='running', description_error=NULL"
        ' WHERE id=?',
        (attachment_id,),
    )
    db.commit()
    _notify_subscribers(row['entry_id'])
    _describe_attachment_bg(attachment_id, row['entry_id'], row['path'], row['name'])
    return jsonify(_attachment_dict(_load_attachment(attachment_id))), 202


def _do_attachment_audio_description(path: str, name: str) -> str:
    from backend.ai.audio_description import describe_audio

    p = storage.resolve_stored_path(path)
    if p is None or not p.is_file():
        raise RuntimeError('The recording is missing')
    return describe_audio(p, hint=name)


def _describe_attachment_bg(attachment_id: str, entry_id: str, path: str, name: str) -> None:
    def _run():
        try:
            text = _do_attachment_audio_description(path, name)
            status, error = 'done', None
        except Exception as e:
            text, status, error = None, 'error', str(e) or 'Failed'
            print(f'Attachment audio description failed for {attachment_id}: {e}')

        try:
            db = get_db()
            updates = {'description_status': status, 'description_error': error}
            if text is not None:
                updates['description'] = text
            build_update(db, 'journal_attachments', updates, 'id=?', (attachment_id,))
            db.commit()
            _notify_subscribers(entry_id)
        except Exception as e:
            print(f'Failed to record audio description result for {attachment_id}: {e}')
    run_bg(_run)


def _do_attachment_audio(path: str) -> str:
    """Transcribe an audio *or video* attachment through whichever STT backend
    is configured, loading it on demand exactly as POST /api/transcribe does.

    Video needs no special handling: every backend goes through ffmpeg (directly
    for Parakeet, internally for Whisper) and ffmpeg reads the audio track out of
    a container without caring that there are also video frames in it.
    """
    # Imported here rather than at module scope: the STT module pulls in numpy
    # and (for the local backend) torch, and the journal blueprint is imported
    # by tests that have no business paying for that.
    from backend.routes import stt as stt_routes

    p = storage.resolve_stored_path(path)
    if p is None or not p.is_file():
        raise RuntimeError('The recording is missing')
    stt_routes._load_stt()
    result = stt_routes._do_transcribe(p.read_bytes(), p.name, None)
    text = (result.get('text') or '').strip()
    if not text:
        raise RuntimeError('No speech found in the recording')
    return text


def _do_attachment_caption(path: str, name: str) -> str:
    from backend.ai.images import caption_image

    p = storage.resolve_stored_path(path)
    if p is None or not p.is_file():
        raise RuntimeError('The image file is missing')
    return caption_image(p, hint=name)


def _transcribe_attachment_bg(
    attachment_id: str, entry_id: str, kind: str, path: str, name: str
) -> None:
    def _run():
        try:
            # Video takes the speech path, not the vision one: what is worth
            # keeping from a clip filmed to talk into is what was said.
            if kind in ('audio', 'video'):
                text = _do_attachment_audio(path)
            else:
                text = _do_attachment_caption(path, name)
            status, error = 'done', None
        except Exception as e:
            text, status, error = None, 'error', str(e) or 'Failed'
            print(f'Attachment transcription failed for {attachment_id}: {e}')

        try:
            db = get_db()
            updates = {'transcript_status': status, 'transcript_error': error}
            if text is not None:
                updates['transcript'] = text
            build_update(db, 'journal_attachments', updates, 'id=?', (attachment_id,))
            db.commit()
            _notify_subscribers(entry_id)
        except Exception as e:
            print(f'Failed to record transcription result for {attachment_id}: {e}')
    run_bg(_run)


def _generate_metadata_bg(journal_id: str, content: str) -> None:
    def _run():
        try:
            meta = generate_journal_metadata(content)
            if not meta:
                return
            updates: dict = {}
            if meta.get('title'):
                updates['title'] = meta['title']
            if meta.get('tags'):
                updates['tags'] = tags_json(meta['tags'])
            if not updates:
                return
            db = get_db()
            build_update(db, 'journal_entries', updates, 'id=?', (journal_id,))
            db.commit()
            _notify_subscribers(journal_id)
        except Exception as e:
            print(f'Background metadata generation failed for {journal_id}: {e}')
    run_bg(_run)


