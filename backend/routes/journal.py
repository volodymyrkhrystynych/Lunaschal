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
from backend.ai import jobs
from backend.ai.service import InferencePaused, PAUSED_MESSAGE, Preempted
from backend.journal import archive, storage, voice_drafts, youtube_import
from backend.study import youtube
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


def _enrich_with_idea_refs(db, dicts: list[dict]) -> list[dict]:
    """Attach the idea a dictated entry belongs to, when it still exists.

    Read through a join rather than trusted from the column: `idea_id` is
    cleared when an idea is deleted, but only where foreign keys are on for that
    connection — and a link pill that opens a 404 is worse than no pill. The
    title falls back to the idea's first line the way the Ideas list does
    (backend/research/idea_text.py), so an idea whose title has not been
    generated yet is still recognisable here.
    """
    if not dicts:
        return dicts
    from backend.research.idea_text import display_title

    ids = [d['id'] for d in dicts if d.get('ideaId')]
    titles: dict[str, str] = {}
    if ids:
        placeholders = ','.join('?' * len(ids))
        rows = db.execute(
            f'SELECT i.id, i.title, i.raw_content, i.content FROM ideas i'
            f' JOIN journal_entries je ON je.idea_id = i.id'
            f' WHERE je.id IN ({placeholders})',
            ids,
        ).fetchall()
        titles = {r['id']: display_title(dict(r)) for r in rows}
    for d in dicts:
        idea_id = d.get('ideaId')
        if idea_id and idea_id in titles:
            d['ideaTitle'] = titles[idea_id]
        else:
            # The idea is gone (or there never was one): drop the dangling id
            # too, so the client has one thing to test rather than two.
            d['ideaId'] = None
            d['ideaTitle'] = None
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
        db,
        _enrich_with_fic_refs(
            db, _enrich_with_idea_refs(db, _enrich_with_curated_tags(db, dicts))
        ),
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
        db,
        _enrich_with_fic_refs(
            db, _enrich_with_idea_refs(db, _enrich_with_curated_tags(db, dicts))
        ),
    ))


@bp.get('/<id>')
def get_entry(id):
    db = get_db()
    row = db.execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_enrich_with_attachments(
        db, _enrich_with_idea_refs(db, [row_to_dict(row)])
    )[0])


def create_journal_entry(
    content: str,
    raw_content: str | None,
    created_at: int,
    *,
    title: str | None = None,
    tags=None,
    entry_id: str | None = None,
    polish: bool = False,
    pending_attachments: int = 0,
) -> str | None:
    """Inserts a journal entry and kicks off background metadata generation
    (and, if `polish` and `raw_content` is set, background polish — the STT
    dictation path). `entry_id` lets a caller supply its own id for an
    idempotent replay (e.g. an offline-queued create, or a scheduler's
    catch-up promotion); a collision is a no-op and returns None.

    `pending_attachments` is how many files the client is about to upload
    against this id. Attachments necessarily arrive *after* the entry — they
    need its id — so without this the title would always be generated from the
    text alone, before any photo had been captioned. Given it, metadata waits.

    A composer mints its entry id at the first recorded chunk, so a clip can
    reach `create_recording_entry` *before* this does — the boot sweep after a
    crash sends only the clip, and an offline queue can replay in either order.
    That leaves a row already here, created empty by the recording route, and a
    plain `INSERT OR IGNORE` would silently drop the words typed alongside the
    audio. So an existing row that is still empty is filled in instead; one that
    already has text is a genuine replay and is left alone.
    """
    id = entry_id or str(ULID())
    db = get_db()
    cur = db.execute(
        'INSERT OR IGNORE INTO journal_entries(id, content, raw_content, title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, content, raw_content, title, tags_json(tags), created_at, created_at),
    )
    db.commit()
    if cur.rowcount == 0:
        if not content.strip():
            return None
        adopted = db.execute(
            "UPDATE journal_entries SET content=?, raw_content=?, updated_at=?"
            " WHERE id=? AND content='' AND COALESCE(raw_content, '')=''",
            (content, raw_content, created_at, id),
        )
        db.commit()
        if not adopted.rowcount:
            return None
        # Deliberately falls through: the entry now has the text it was created
        # with, so it still needs its polish and its title. Its clips' own
        # transcripts append after this, and their passes re-run over the lot.
        _notify_subscribers(id)
        if polish and raw_content:
            _polish_bg(id, raw_content)
        if not title or not tags:
            _generate_metadata_bg(id, content, expect_attachments=pending_attachments)
        return id
    _notify_subscribers(id)
    if polish and raw_content:
        _polish_bg(id, raw_content)
    if not title or not tags:
        _generate_metadata_bg(id, content, expect_attachments=pending_attachments)
    return id


@bp.post('')
def create_entry():
    body = request.json or {}
    raw_content = body.get('raw_content', '').strip()
    content = body.get('content', '').strip()

    try:
        pending = int(body.get('pendingAttachments') or 0)
    except (TypeError, ValueError):
        pending = 0
    # Clamped: this only ever delays a title, but an unbounded value from the
    # wire would park a thread on a condition that can never become true until
    # the cap ran out.
    pending = max(0, min(pending, 20))

    if raw_content:
        # STT path: save immediately with raw text, polish in background
        content = raw_content
    else:
        raw_content = None
        # An entry whose whole content is a photograph is a real entry, and the
        # composer offers Save for one: its button is enabled on a staged file
        # with no text at all. Refusing it here used to 400 that save, and the
        # cost did not stop at the error -- the photo is queued behind the
        # create in JOURNAL_LANE, so it then POSTed against an entry id that
        # would never exist, and its 404 is deliberately treated as "the create
        # is still coming" rather than terminal. One rejected save left a file
        # retrying forever, once per app launch.
        #
        # `pending` is what makes the empty body legible: it says files are on
        # their way to this id, which is the same promise that already makes
        # title generation wait for their captions. Empty with nothing coming is
        # still an accident and still refused.
        if not content and not pending:
            return jsonify({'error': 'content required'}), 400

    title = body.get('title') or None
    tags = body.get('tags') or None

    now = int(time.time())
    # Accept a client-supplied ULID so an offline-queued create replays
    # idempotently: create_journal_entry's INSERT OR IGNORE makes a duplicate
    # a no-op, and a None return means we've already saved this entry.
    id = body.get('id') or str(ULID())
    create_journal_entry(
        content, raw_content, now,
        title=title, tags=tags, entry_id=id, polish=True,
        pending_attachments=pending,
    )
    return jsonify({'id': id}), 201


def _attachment_polish_context(entry_id: str) -> str | None:
    """Non-speech descriptions of the entry's audio/video attachments, offered
    to the polish model as context for fixing a misheard word — the
    description comes from a different model/listener, and may have gotten a
    name right that raw_content's speech-to-text did not (see the 'Context:'
    handling in backend/ai/journal.py's system prompt)."""
    rows = get_db().execute(
        "SELECT name, description FROM journal_attachments"
        " WHERE entry_id=? AND kind IN ('audio','video','youtube')"
        " AND description_status='done' AND description IS NOT NULL",
        (entry_id,),
    ).fetchall()
    if not rows:
        return None
    return '\n'.join(f"{r['name']}: {r['description']}" for r in rows)


def _polish_context(entry_id: str) -> str | None:
    """Everything the polish model can check a misheard word against: the
    standing memory document (names already known about the user) plus the
    entry's own audio/video attachment descriptions. The same two references
    Chat dictation's now-removed correction pass used, folded into Journal's
    polish instead — one place to fix a misheard word, not two."""
    from backend.memory import get_memory

    parts = []
    memory = get_memory()
    if memory and memory.strip():
        parts.append(f'Things already known about the user:\n{memory.strip()}')
    attachments = _attachment_polish_context(entry_id)
    if attachments:
        parts.append(f"This entry's audio/video attachments:\n{attachments}")
    return '\n\n'.join(parts) if parts else None


@bp.post('/<id>/polish')
def polish_entry(id):
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    source = row['raw_content'] or ''
    if not source.strip():
        return jsonify({'error': 'No original transcription to polish'}), 400
    try:
        polished = polish_journal_entry(source, context=_polish_context(id))
    except PolishUnavailable as e:
        # Leave `content` exactly as it is. Writing the raw transcript back here
        # is what used to make an offline llama-server look like a broken button.
        # Both are 503, but a *paused* GPU is a state the user can fix from
        # Settings, so it is flagged for the banner rather than reported as
        # another way the model is broken.
        if getattr(e, 'paused', False):
            return jsonify({'error': PAUSED_MESSAGE, 'inferencePaused': True}), 503
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
        db,
        _enrich_with_fic_refs(
            db, _enrich_with_idea_refs(db, _enrich_with_curated_tags(db, [merged]))
        ),
    )[0])


def _polish_bg(journal_id: str, raw_content: str, *, now: bool = False) -> None:
    def _run():
        try:
            polished = polish_journal_entry(
                raw_content, context=_polish_context(journal_id)
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

    if now:
        _run()
    else:
        jobs.enqueue('journal.polish', journal_id, {'raw_content': raw_content})


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

# A `file` attachment is anything the media tables didn't claim. The cap sits
# between audio and video: generous enough for a PDF, a zip or a spreadsheet,
# not so generous that a mis-picked disk image fills ./data.
MAX_FILE_BYTES = 250 * 1024 * 1024

_MAX_BYTES_BY_KIND = {
    'audio': MAX_AUDIO_BYTES,
    'image': MAX_IMAGE_BYTES,
    'video': MAX_VIDEO_BYTES,
    'file': MAX_FILE_BYTES,
}

_UNSUPPORTED_TYPE = 'Unsupported file type'

_DEFAULT_NAMES = {
    'audio': 'Audio', 'video': 'Video', 'image': 'Photo', 'file': 'File',
}

_ATTACHMENT_COLS = (
    'id, entry_id, kind, name, path, mime, size, position,'
    ' transcript, transcript_status, transcript_error,'
    ' description, description_status, description_error,'
    ' latitude, longitude, source_url, duration_seconds, thumb_path,'
    ' import_status, import_error, created_at'
)


def _attachment_dict(row) -> dict:
    d = row_to_dict(row)
    # `path` and `thumb_path` are server-side filesystem locations; the client
    # gets URLs instead.
    d.pop('path', None)
    thumb = d.pop('thumbPath', None)
    # Only when there is something to serve. A youtube attachment exists from
    # the moment the link is pasted and has no file until the download lands —
    # handing the client a URL to it then is a broken <video> on every card.
    if row['path']:
        d['url'] = f'/api/journal/attachments/{row["id"]}/file'
    if thumb:
        d['thumbnailUrl'] = f'/api/journal/attachments/{row["id"]}/thumbnail'
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
    entry_id: str, file, name: str | None, attachment_id: str | None = None,
    *, media_only: bool = False,
):
    """Save one uploaded file as an attachment of `entry_id`.

    Returns `(attachment_dict, None)` on success and `(None, (error, status))`
    on a rejected upload. Split out of the upload route so the one-shot
    recording route below can reuse it — the two differ only in where the entry
    comes from, not in how a file becomes an attachment.

    `media_only` refuses the `file` catch-all kind. The composer's attach-a-file
    button wants that catch-all; the recording route does not — a `text/plain`
    arriving there means the phone sent the wrong blob, and storing it as a
    curiosity would hide a bug behind a 201.

    `attachment_id` lets a caller supply the id instead of minting one. Only the
    recording route does: a phone holds its audio until the upload is confirmed,
    so the same clip is re-POSTed until it lands, and a client-chosen id is what
    lets the replay be recognized (see `create_recording_entry`).
    """
    # NOT `or not file.filename`: a voice memo dragged out of the iOS Voice Memos
    # app arrives as a File with an empty name, and rejecting it here turned a
    # working drag-and-drop into "file is required". A nameless upload is fine —
    # the mime type carries the extension and _DEFAULT_NAMES carries the label.
    ext, kind = storage.resolve_upload(file.mimetype, file.filename)
    if media_only and kind == 'file':
        return None, (_UNSUPPORTED_TYPE + ' — audio, video or images only', 400)

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

    # A photo's caption now fires on upload too, on the same terms, because the
    # entry's *title* is generated from it (see `_generate_metadata_bg`). Waiting
    # for someone to press Transcribe means the title never sees the picture,
    # which was the whole complaint. Audio/video transcription stays opt-in: it
    # is minutes of Whisper, not one vision call.
    from backend.ai.images import is_vision_configured
    auto_caption = kind == 'image' and is_vision_configured()
    transcript_status = 'running' if auto_caption else 'idle'

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
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (attachment_id, entry_id, kind, name, str(path), file.mimetype or None,
             size, position, transcript_status, description_status,
             latitude, longitude, int(time.time())),
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
    if auto_caption:
        _transcribe_attachment_bg(attachment_id, entry_id, kind, str(path), name)
    return _attachment_dict(_load_attachment(attachment_id)), None


@bp.post('/<id>/attachments')
def upload_attachment(id):
    """Attach a file to an entry that already exists.

    **Replaying this request must be a no-op**, for the same reason the
    recording route's replay must be: the composer now holds a staged photo in
    IndexedDB and re-POSTs it until the server confirms it, so a retry after a
    response that never made it back is the normal case on a phone. An optional
    client-supplied `attachmentId` is what lets the replay be recognized, and
    the early return below happens *before* the file is read so a replay does
    not stream the picture to disk again to discover it was already there.

    Without the id (paste and drop on the desktop, which upload once and
    surface their own error) every call mints a fresh one and behaves as it
    always has.
    """
    try:
        attachment_id = _client_id(request.form.get('attachmentId'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if attachment_id:
        existing = _load_attachment(attachment_id)
        if existing is not None:
            # Answer as if we'd just saved it so the client lets go of its local
            # copy — the whole point of the retry loop. Deliberately not checked
            # against `id`: an attachment we already hold is stored, and moving
            # it would be a stranger outcome than ignoring the duplicate POST.
            return jsonify(_attachment_dict(existing)), 201

    entry = get_db().execute(
        'SELECT id FROM journal_entries WHERE id=?', (id,)
    ).fetchone()
    if not entry:
        return jsonify({'error': 'Not found'}), 404

    file = request.files.get('file')
    if file is None:
        return jsonify({'error': 'file is required'}), 400

    attachment, failure = _store_attachment(
        id, file, request.form.get('name'), attachment_id
    )
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


def _link_recording_idea(entry_id: str, idea_id: str, repo_id=None) -> None:
    """Open the idea half of an Ideas-tab recording and point the entry at it."""
    from backend.routes.ideas import create_recording_idea

    create_recording_idea(idea_id, repo_id)
    db = get_db()
    db.execute('UPDATE journal_entries SET idea_id=? WHERE id=?', (idea_id, entry_id))
    db.commit()


def _link_recording_fic(entry_id: str, fic_id: str, chapter_id: str | None) -> None:
    """Point a recording's entry at the fic (and chapter) it was dictated over.

    The reader's commentary microphone sends these ids *with* the upload rather
    than linking in a second request, for the same reason `ideaId` is on this
    endpoint at all: the upload is replayed until it lands, so a link made by a
    separate call is the one step in the sequence that a dropped connection can
    lose — leaving the commentary in the journal with nothing saying which
    chapter it was about.

    Idempotent like every other half of this route: the same (entry, fic,
    chapter) triple inserts once. A fic or chapter that no longer exists is
    dropped rather than failing the upload — the audio is the irreplaceable
    half, and an entry that lost its link is still the entry.
    """
    db = get_db()
    if not db.execute('SELECT id FROM fics WHERE id=?', (fic_id,)).fetchone():
        print(f'Recording {entry_id} references unknown fic {fic_id}; link skipped')
        return
    if chapter_id and not db.execute(
        'SELECT id FROM fic_chapters WHERE id=? AND fic_id=?', (chapter_id, fic_id)
    ).fetchone():
        print(f'Recording {entry_id} references unknown chapter {chapter_id}; '
              'linking to the fic alone')
        chapter_id = None
    existing = db.execute(
        'SELECT id FROM journal_entry_fic_refs'
        ' WHERE journal_entry_id=? AND fic_id=? AND chapter_id IS ?',
        (entry_id, fic_id, chapter_id),
    ).fetchone()
    if existing:
        return
    db.execute(
        'INSERT INTO journal_entry_fic_refs(id, journal_entry_id, fic_id, chapter_id, created_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), entry_id, fic_id, chapter_id, int(time.time())),
    )
    db.commit()


@bp.post('/recordings')
def create_recording_entry():
    """Save a recording as a journal entry, optionally transcribing it.

    Both bottom-bar journal buttons finish here. Record leaves the entry body
    empty; Journal passes `transcribe=true`, which queues the stored attachment
    for transcription into the entry body. Uploading first means the original
    recording survives either path and any later transcription failure.

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

    `ideaId` is the Ideas tab's Record button, where the same clip is also an
    idea. One upload, one transcription, two rows: the entry below and an empty
    idea, linked by `journal_entries.idea_id` and filled in together when the
    transcript lands. That id is the client's too, for the same replay reason.

    `ficId` (with an optional `chapterId`) is the fanfic reader's commentary
    microphone, and rides along here for the same reason: the entry it makes is
    commentary *on a chapter*, and the link that says so has to survive the
    replay loop along with the audio. See `_link_recording_fic`.
    """
    try:
        entry_id = _client_id(request.form.get('id'))
        attachment_id = _client_id(request.form.get('attachmentId'))
        idea_id = _client_id(request.form.get('ideaId'))
        fic_id = _client_id(request.form.get('ficId'))
        chapter_id = _client_id(request.form.get('chapterId'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    db = get_db()
    transcribe = request.form.get('transcribe', '').lower() in ('1', 'true', 'yes')

    if attachment_id:
        existing = _load_attachment(attachment_id)
        if existing is not None:
            if idea_id:
                # Converge rather than assume: the first call could have stored
                # the file and died before writing the idea. Both statements are
                # idempotent, so a normal replay changes nothing.
                _link_recording_idea(existing['entry_id'], idea_id,
                                     request.form.get('repoId'))
            if fic_id:
                # Same convergence, same reason: a first call that stored the
                # clip and died before the link would otherwise leave the
                # commentary permanently detached from its chapter.
                _link_recording_fic(existing['entry_id'], fic_id, chapter_id)
            if transcribe:
                _queue_attachment_transcription(
                    existing, into_entry=True, skip_completed=True
                )
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
            entry_id, file, request.form.get('name') or 'Recording', attachment_id,
            media_only=True,
        )
    except Exception:
        _rollback_entry()
        raise
    if failure is not None:
        _rollback_entry()
        message, status = failure
        return jsonify({'error': message}), status

    if idea_id:
        # After the file, not before: a rejected upload rolls the entry back,
        # and an idea left pointing at nothing would be a permanently empty row
        # in the backlog with no recording to explain it.
        _link_recording_idea(entry_id, idea_id, request.form.get('repoId'))

    if fic_id:
        # After the file for the same reason: a fic ref to a rolled-back entry
        # would put a dead row in the reader's commentary history.
        _link_recording_fic(entry_id, fic_id, chapter_id)

    _notify_subscribers(entry_id)
    if transcribe:
        _queue_attachment_transcription(_load_attachment(attachment['id']), into_entry=True)
    return jsonify({'id': entry_id, 'attachment': attachment, 'ideaId': idea_id,
                    'ficId': fic_id}), 201


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


@bp.post('/attachments/<attachment_id>/rotate')
def rotate_attachment(attachment_id):
    """Permanently rotate one journal photo 90 degrees clockwise."""
    row = _load_attachment(attachment_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['kind'] != 'image':
        return jsonify({'error': 'Only image attachments can be rotated'}), 400
    path = storage.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Image file not found'}), 404

    try:
        from backend.imaging import rotate_clockwise
        size = rotate_clockwise(path)
    except Exception as e:
        return jsonify({'error': f'Could not rotate image: {e}'}), 422

    db = get_db()
    db.execute('UPDATE journal_attachments SET size=? WHERE id=?',
               (size, attachment_id))
    db.commit()
    _notify_subscribers(row['entry_id'])
    return jsonify(_attachment_dict(_load_attachment(attachment_id)))


@bp.delete('/attachments/<attachment_id>')
def delete_attachment(attachment_id):
    db = get_db()
    row = _load_attachment(attachment_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    entry_id = row['entry_id']
    # Cancel first, so a download thread still running reads its absence from
    # the progress registry as cancellation and stops writing to a dead row.
    youtube_import.cancel_progress(attachment_id)
    db.execute('DELETE FROM journal_attachments WHERE id=?', (attachment_id,))
    db.commit()
    storage.delete_attachment_dir(attachment_id)
    if row['kind'] == 'youtube':
        archive.delete_archived_dir(attachment_id)
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
    path = _resolve_attachment_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    # conditional=True so <audio> range requests work — seeking in a long voice
    # memo otherwise re-downloads the whole file on every scrub.
    return send_file(path, mimetype=row['mime'] or None, conditional=True)


@bp.post('/<entry_id>/attachments/link')
def attach_link(entry_id):
    """Attach a YouTube video to an entry by its URL.

    The row is created synchronously and the bytes arrive minutes later, so this
    returns 201 with an attachment already in `import_status='importing'` — the
    card can show itself downloading instead of the composer blocking on a
    thirty-minute fetch.

    Replay is a no-op, same contract as the upload route above: an optional
    client-supplied `attachmentId` is what lets a re-POST be recognised, and the
    early return happens before the download thread is spawned so a retry does
    not start a second one.
    """
    body = request.get_json(silent=True) or {}
    url = (body.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'Missing url'}), 400
    try:
        attachment_id = _client_id(body.get('attachmentId'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if attachment_id:
        existing = _load_attachment(attachment_id)
        if existing is not None:
            return jsonify(_attachment_dict(existing)), 201
    else:
        attachment_id = str(ULID())

    db = get_db()
    entry = db.execute(
        'SELECT id FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    if entry is None:
        # The create is still in flight. The client retries rather than
        # surfacing this, exactly as it does for a staged photo.
        return jsonify({'error': 'Entry not found'}), 404

    # Rejected here as well as in the worker: this is the one caller that can
    # answer the user directly, and a 400 beats a card that appears only to
    # fail. The worker still checks, because it is also reached by a retry.
    if youtube.parse_video_id(url) is None:
        return jsonify({'error': 'That does not look like a YouTube video URL.'}), 400

    position = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next FROM journal_attachments'
        ' WHERE entry_id=?',
        (entry_id,),
    ).fetchone()['next']

    # `transcript_status` stays 'idle', not 'running': it is what
    # `_attachments_settled` reads, and a download that can take half an hour
    # would otherwise hold the entry's title generation for its full wait cap
    # and then time out anyway. The download has its own `import_status`.
    cur = db.execute(
        'INSERT OR IGNORE INTO journal_attachments'
        '(id, entry_id, kind, name, path, position, source_url,'
        " import_status, created_at)"
        " VALUES (?,?,'youtube',?,'',?,?,'importing',?)",
        (attachment_id, entry_id, url, position, url, int(time.time())),
    )
    db.commit()

    # OR IGNORE, and the rowcount matters: two replays of the same link can both
    # pass the existence check above before either has inserted. Losing that
    # race must be a no-op — spawning the thread anyway is a second yt-dlp
    # process writing into the same directory as the first.
    if cur.rowcount:
        _notify_subscribers(entry_id)
        youtube_import.start_progress(attachment_id, 'queued')
        youtube_import.start_import_bg(attachment_id, entry_id, url)
    return jsonify(_attachment_dict(_load_attachment(attachment_id))), 201


@bp.get('/attachments/<attachment_id>/import-status')
def attachment_import_status(attachment_id):
    """What the download is doing right now, for the card's progress line.

    `{'done': True}` when the registry has nothing: either it finished, or this
    process never started it (a restart), and the row's own `import_status` is
    the durable answer in both cases.
    """
    return jsonify(youtube_import.get_progress(attachment_id) or {'done': True})


@bp.get('/attachments/<attachment_id>/thumbnail')
def get_attachment_thumbnail(attachment_id):
    """The video's poster. On the SSD even when the video is on the archive
    drive, so this still answers with the drive unplugged."""
    row = get_db().execute(
        'SELECT thumb_path FROM journal_attachments WHERE id=?', (attachment_id,)
    ).fetchone()
    if not row or not row['thumb_path']:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['thumb_path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype='image/jpeg', conditional=True)


@bp.post('/attachments/<attachment_id>/transcribe')
def transcribe_attachment(attachment_id):
    """Queue transcription (audio/video) or captioning (image) for one attachment.

    Returns 202 immediately; the result arrives on the row and is pushed to the
    client through the /events stream.
    """
    row = _load_attachment(attachment_id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    # `file` is the catch-all kind behind the composer's attach-a-file button.
    # There is no model that reads an arbitrary blob, and queueing one would
    # park the row in 'running' until it errored.
    if row['kind'] == 'file':
        return jsonify({'error': 'This attachment type cannot be transcribed'}), 400
    if row['kind'] == 'youtube' and row['import_status'] != 'ready':
        return jsonify({'error': 'The video is still downloading'}), 409
    if row['transcript_status'] == 'running':
        return jsonify({'error': 'Already running'}), 409
    _queue_attachment_transcription(row)
    return jsonify(_attachment_dict(_load_attachment(attachment_id))), 202


def _queue_attachment_transcription(
    row, *, into_entry: bool = False, skip_completed: bool = False
) -> None:
    """Queue one attachment once; upload replays may safely call this again."""
    if row['transcript_status'] == 'running' or (
        skip_completed and row['transcript_status'] == 'done'
    ):
        return
    db = get_db()
    db.execute(
        "UPDATE journal_attachments SET transcript_status='running', transcript_error=NULL"
        ' WHERE id=?',
        (row['id'],),
    )
    db.commit()
    _notify_subscribers(row['entry_id'])
    _transcribe_attachment_bg(row['id'], row['entry_id'], row['kind'],
                              row['path'], row['name'], into_entry=into_entry)


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


def _describe_attachment_bg(attachment_id: str, entry_id: str, path: str,
                            name: str, *, now: bool = False) -> None:
    def _run():
        try:
            text = _do_attachment_audio_description(path, name)
            status, error = 'done', None
        except (InferencePaused, Preempted):
            # Neither is a failure of this attachment, and neither is this
            # function's to record: the llm_jobs row is what remembers, and the
            # worker requeues it. Writing 'error' here — which is what the
            # catch-all below did — turned "the GPU is off for the evening" into
            # a permanent failure with a job marked `done` behind it, which is
            # how an evening of screenshots ended up with no captions and
            # nothing queued to make them. Leaving the row alone means it reads
            # `idle` after an upload and `running` after the retry button, and
            # `_reset_stale_attachment_transcripts` turns the second into the
            # first on the next restart — all three of which are true while the
            # job waits, and none of which is an error the user must clear.
            raise
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

    if now:
        _run()
    else:
        jobs.enqueue('journal.describe_audio', attachment_id,
                     {'entry_id': entry_id, 'path': path, 'name': name})


def _resolve_attachment_path(path: str):
    """The file behind an attachment row, or None.

    Two roots, because a downloaded video is the one attachment whose bytes are
    not under JOURNAL_ROOT — it lives on the archive drive
    (backend/journal/archive.py). Tried in turn rather than selected by `kind`,
    so every caller that has a path but no row stays a one-argument call.

    That is safe because neither resolver is a prefix test it can be talked out
    of: each one independently requires the path to still be a direct grandchild
    of *its own* root, so a `path` column that has since been tampered with
    resolves to None under both.
    """
    if not path:
        return None
    return storage.resolve_stored_path(path) or archive.resolve_archived_path(path)


def _do_attachment_audio(path: str) -> str:
    """Transcribe an audio, video *or downloaded YouTube* attachment.

    The work itself lives in `stt.transcribe_file`, shared with the food log's
    meal clips — this resolves the stored path first, which is journal-specific.
    A video container needs no special handling: every STT backend goes through
    ffmpeg, which reads the audio track out without caring about the frames.
    """
    # Imported here rather than at module scope: the STT module pulls in numpy
    # and (for the local backend) torch, and the journal blueprint is imported
    # by tests that have no business paying for that.
    from backend.routes import stt as stt_routes

    p = _resolve_attachment_path(path)
    if p is None or not p.is_file():
        raise RuntimeError('The recording is missing')
    return stt_routes.transcribe_file(p)


def _summarize_youtube_bg(
    attachment_id: str, entry_id: str, title: str = '', *, now: bool = False
) -> None:
    """Write the few sentences saying what a watched video was about.

    Lands on `description`, beside the transcript in `transcript` — the same
    split the audio attachments use, where the transcript is the record and the
    description is the summary of it.

    Never touches the entry's own text. The entry is the user's commentary; a
    summary folded into it would be an AI paragraph inside the one field the
    journal promises is verbatim.
    """
    def _run():
        from backend.ai.youtube import summarize_video

        try:
            row = get_db().execute(
                'SELECT transcript, name FROM journal_attachments WHERE id=?',
                (attachment_id,),
            ).fetchone()
            if row is None:
                return
            transcript = row['transcript'] or ''
            if not transcript.strip():
                return
            text = summarize_video(title or row['name'] or '', transcript)
            status, error = ('done', None) if text else ('idle', None)
        except (InferencePaused, Preempted):
            # Same contract as _transcribe_attachment_bg's: the llm_jobs row is
            # what remembers, and recording 'error' here would turn a paused
            # evening into a permanent failure behind a job marked done.
            raise
        except Exception as e:
            text, status, error = None, 'error', str(e) or 'Failed'
            print(f'Video summarization failed for {attachment_id}: {e}')

        try:
            db = get_db()
            updates = {'description_status': status, 'description_error': error}
            if text:
                updates['description'] = text
            build_update(db, 'journal_attachments', updates, 'id=?', (attachment_id,))
            db.commit()
            _notify_subscribers(entry_id)
        except Exception as e:
            print(f'Failed to record video summary for {attachment_id}: {e}')

    if now:
        _run()
    else:
        jobs.enqueue('journal.summarize_youtube', attachment_id,
                     {'entry_id': entry_id, 'title': title})


def _do_attachment_caption(path: str, name: str) -> str:
    from backend.ai.images import caption_image

    p = storage.resolve_stored_path(path)
    if p is None or not p.is_file():
        raise RuntimeError('The image file is missing')
    return caption_image(p, hint=name)


def _deliver_idea_transcript(entry_id: str, text: str) -> None:
    """Hand the transcript to the idea this entry was dictated for, if any.

    The link is re-read here rather than captured when the transcription was
    queued: minutes can pass, and an idea deleted in between should take no
    further writes. `apply_recording_transcript` is guarded on the same thing
    from its side.
    """
    row = get_db().execute(
        'SELECT idea_id FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    if not row or not row['idea_id']:
        return
    from backend.routes.ideas import apply_recording_transcript

    apply_recording_transcript(row['idea_id'], text)


def _append_entry_text(db, entry_id: str, text: str) -> None:
    """Add a clip's transcript to the end of an entry's body.

    Both columns move together: `raw_content` is what the polish pass reads and
    `content` is what the feed shows until that pass replaces it, so an entry
    whose polish has not run yet still reads correctly.

    A blank line between clips, not a space — the gap is the pause the user
    took, and it is the only thing distinguishing "one thought, said twice"
    from a run-on sentence.
    """
    row = db.execute(
        'SELECT content, raw_content FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    if row is None:
        return
    existing = (row['raw_content'] or row['content'] or '').strip()
    merged = f'{existing}\n\n{text}' if existing else text
    db.execute(
        'UPDATE journal_entries SET content=?, raw_content=?, updated_at=? WHERE id=?',
        (merged, merged, int(time.time()), entry_id),
    )


def _entry_raw_text(entry_id: str) -> str | None:
    row = get_db().execute(
        'SELECT content, raw_content FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    if row is None:
        return None
    return row['raw_content'] or row['content'] or None


def _transcribe_attachment_bg(
    attachment_id: str, entry_id: str, kind: str, path: str, name: str,
    *, into_entry: bool = False, now: bool = False,
) -> None:
    def _run():
        try:
            # Video takes the speech path, not the vision one: what is worth
            # keeping from a clip filmed to talk into is what was said.
            if kind in ('audio', 'video', 'youtube'):
                text = _do_attachment_audio(path)
            else:
                text = _do_attachment_caption(path, name)
            status, error = 'done', None
        except (InferencePaused, Preempted):
            # Neither is a failure of this attachment, and neither is this
            # function's to record: the llm_jobs row is what remembers, and the
            # worker requeues it. Writing 'error' here — which is what the
            # catch-all below did — turned "the GPU is off for the evening" into
            # a permanent failure with a job marked `done` behind it, which is
            # how an evening of screenshots ended up with no captions and
            # nothing queued to make them. Leaving the row alone means it reads
            # `idle` after an upload and `running` after the retry button, and
            # `_reset_stale_attachment_transcripts` turns the second into the
            # first on the next restart — all three of which are true while the
            # job waits, and none of which is an error the user must clear.
            raise
        except Exception as e:
            text, status, error = None, 'error', str(e) or 'Failed'
            print(f'Attachment transcription failed for {attachment_id}: {e}')

        try:
            db = get_db()
            # Whether this attachment has ever produced a transcript before.
            # Read *before* the update, because it is what decides between
            # appending to the entry and merely refreshing the clip's own text:
            # re-running Transcribe on a clip must not paste it into the entry
            # a second time.
            prior = db.execute(
                'SELECT transcript FROM journal_attachments WHERE id=?',
                (attachment_id,),
            ).fetchone()
            first_time = not (prior and prior['transcript'])

            updates = {'transcript_status': status, 'transcript_error': error}
            if text is not None:
                updates['transcript'] = text
            build_update(db, 'journal_attachments', updates, 'id=?', (attachment_id,))

            merge = text is not None and into_entry and first_time
            if merge:
                # Appended, not assigned. A composer stages several clips
                # against one entry and they are uploaded in record order, so
                # the entry reads as one passage with a blank line where each
                # pause was — the transcript of a second thought used to
                # overwrite the first.
                _append_entry_text(db, entry_id, text)
            db.commit()
            _notify_subscribers(entry_id)

            if merge:
                # The whole entry so far, not this clip alone: polish and
                # titling both read the body, and handing them one clip out of
                # three titles the entry after its first sentence.
                body = _entry_raw_text(entry_id) or text
                # Re-run per clip rather than waiting for the last one. It is
                # not possible to know here that a later clip is coming — its
                # upload may not have reached the server yet, so "no attachment
                # is still running" can be true in the gap between two of them,
                # and a pass skipped on that basis would never happen at all.
                # Running again is safe because these three jobs share one FIFO
                # worker: the version enqueued from the last clip is the last
                # one to write, and it is the one that saw the whole text.
                #
                # The idea first: it is the tab the recording was made in, so
                # it is the one being watched.
                _deliver_idea_transcript(entry_id, body)
                _polish_bg(entry_id, body)
                _generate_metadata_bg(entry_id, body)

            # A watched video's words are not the entry's words (so `merge` is
            # false for one), but they are the only thing the summary can be
            # written from — and this is the point at which they exist. Queued
            # here rather than beside the transcription job because the two
            # share one FIFO worker: enqueued together, the summarizer would run
            # first and read an empty transcript.
            if kind == 'youtube' and text:
                jobs.enqueue('journal.summarize_youtube', attachment_id,
                             {'entry_id': entry_id, 'title': name})
        except Exception as e:
            print(f'Failed to record transcription result for {attachment_id}: {e}')

    if now:
        _run()
    else:
        jobs.enqueue('journal.transcribe_attachment', attachment_id,
                     {'entry_id': entry_id, 'kind': kind, 'path': path,
                      'name': name, 'into_entry': into_entry})


# --- Voice drafts -------------------------------------------------------------
#
# A clip recorded via the STT listener's Journal hotkey. Unlike /recordings
# above (an intentionally text-free entry), a voice draft is meant to become
# entry text — but instead of transcribing it on the spot with whichever
# single STT model happens to be loaded, several local backends transcribe it
# in the background and the main LLM reconciles their outputs into one entry.
# See backend/journal/voice_drafts.py for the pipeline; these routes are thin
# wrappers over it.

@bp.post('/voice-drafts')
def create_voice_draft():
    try:
        draft_id = _client_id(request.form.get('id'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not draft_id:
        return jsonify({'error': 'id required'}), 400

    file = request.files.get('audio')
    if file is None:
        return jsonify({'error': 'audio is required'}), 400

    draft, failure = voice_drafts.create_draft(draft_id, file)
    if failure is not None:
        message, status = failure
        return jsonify({'error': message}), status
    return jsonify(draft), 201


@bp.get('/voice-drafts')
def list_voice_drafts():
    return jsonify(voice_drafts.list_drafts())


@bp.get('/voice-drafts/<id>/file')
def get_voice_draft_file(id):
    row = get_db().execute(
        'SELECT path, mime FROM journal_voice_drafts WHERE id=?', (id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    path = voice_drafts.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype=row['mime'] or None, conditional=True)


@bp.delete('/voice-drafts/<id>')
def delete_voice_draft(id):
    if not voice_drafts.delete_draft(id):
        return jsonify({'error': 'Not found, or already promoted to an entry'}), 404
    return jsonify({'success': True})


@bp.post('/voice-drafts/<id>/retry')
def retry_voice_draft(id):
    if not voice_drafts.retry_draft(id):
        return jsonify({'error': 'Not found, or not in an error state'}), 404
    return jsonify({'success': True})


# How long the title waits for a photo to be captioned before giving up and
# titling the text alone. Generous on purpose: one full-res photo through a
# CPU-resident projector was measured at 55-113 s, and several attachments queue
# behind each other on one background worker. The cap exists for the case that
# never resolves — an upload that failed after the entry was created, or a tab
# closed between the two requests — not as a latency budget.
_METADATA_WAIT_SECONDS = 300.0
_METADATA_POLL_SECONDS = 2.0


# Live metadata waiters: the thread, and the event that tells it to give up.
# A dict rather than a set of pairs so a waiter can remove itself by the one
# handle it has — a set of tuples invites a `discard(thread)` that silently
# matches nothing and leaks the entry. Only ever touched under the lock;
# `cancel_metadata_waits`/`wait_metadata_idle` are the drain contract every
# other background worker in this app already exposes.
_metadata_waiters: dict[threading.Thread, threading.Event] = {}
_metadata_waiters_lock = threading.Lock()


def cancel_metadata_waits() -> None:
    """Tell every waiting metadata thread to give up.

    Production never calls this — a wait is at most `_METADATA_WAIT_SECONDS` and
    the process outlives it. The test suite does, before it closes the database
    these threads read.
    """
    with _metadata_waiters_lock:
        stops = list(_metadata_waiters.values())
    for stop in stops:
        stop.set()


def wait_metadata_idle(timeout: float = 10.0) -> bool:
    """Block until the waiting threads are gone. True if they went.

    Pair it with `cancel_metadata_waits()`; on its own it would sit through the
    full cap.
    """
    deadline = time.monotonic() + timeout
    with _metadata_waiters_lock:
        threads = list(_metadata_waiters)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return not any(thread.is_alive() for thread in threads)


def _metadata_context(entry_id: str) -> str | None:
    """Captions of the entry's photos, for the title/tags call.

    Images only. Audio and video have their own description column and their own
    consumer (`_attachment_polish_context`); a transcript of speech is already
    the entry's text in the dictation path, and feeding it back in would title
    the entry from a duplicate of itself.
    """
    rows = get_db().execute(
        'SELECT name, transcript FROM journal_attachments'
        " WHERE entry_id=? AND kind='image'"
        " AND transcript_status='done' AND transcript IS NOT NULL"
        ' ORDER BY position',
        (entry_id,),
    ).fetchall()
    if not rows:
        return None
    return '\n'.join(f"{r['name']}: {r['transcript']}" for r in rows)


def _attachments_settled(entry_id: str, expected: int) -> bool:
    """True once every attachment the client said it would upload has arrived
    and none is still being captioned.

    Both halves are needed. Counting rows alone races the upload requests, which
    the composer sends one at a time after the create; checking statuses alone
    would look settled at the instant the entry exists, before any of them is
    there.
    """
    rows = get_db().execute(
        'SELECT transcript_status FROM journal_attachments WHERE entry_id=?',
        (entry_id,),
    ).fetchall()
    if len(rows) < expected:
        return False
    return not any(r['transcript_status'] == 'running' for r in rows)


def _generate_metadata_bg(
    journal_id: str, content: str, *, expect_attachments: int = 0,
    now: bool = False,
) -> None:
    def _run():
        try:
            meta = generate_journal_metadata(content, _metadata_context(journal_id))
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

    if now:
        _run()
        return
    if expect_attachments <= 0:
        jobs.enqueue('journal.metadata', journal_id, {'content': content})
        return

    stop = threading.Event()

    def _wait_then_run():
        try:
            deadline = time.monotonic() + _METADATA_WAIT_SECONDS
            while time.monotonic() < deadline:
                try:
                    if _attachments_settled(journal_id, expect_attachments):
                        break
                except Exception as e:
                    print(f'Metadata wait failed for {journal_id}: {e}')
                    break
                # `stop.wait`, not `time.sleep`: it doubles as the cancellation
                # point, so a cancelled waiter goes away now rather than in up
                # to a poll interval — which is the difference between the test
                # suite draining cleanly and closing the database underneath it.
                if stop.wait(_METADATA_POLL_SECONDS):
                    return
            if stop.is_set():
                return
            jobs.enqueue('journal.metadata', journal_id, {'content': content})
        finally:
            with _metadata_waiters_lock:
                _metadata_waiters.pop(waiter, None)

    # A plain daemon thread, deliberately NOT a queued job. That queue has a
    # single worker shared with the captioning jobs this is waiting for, so a job
    # that blocked on them would head-of-line block the work that unblocks it —
    # a guaranteed deadlock until the cap expired. Nothing here touches a model;
    # it sleeps and reads one indexed row set.
    #
    # It is registered because it is *not* on one of the app's executors, and
    # therefore not covered by any of their `wait_idle`s. It reads the
    # module-global SQLite connection, so a test that closes that connection
    # while this thread is mid-query segfaults the interpreter rather than
    # raising — see backend/tests/conftest.py, which drains every other
    # background worker for exactly this reason. Registering it is what lets it
    # be drained too.
    waiter = threading.Thread(
        target=_wait_then_run, daemon=True, name=f'journal-meta-{journal_id[:8]}'
    )
    with _metadata_waiters_lock:
        _metadata_waiters[waiter] = stop
    waiter.start()
