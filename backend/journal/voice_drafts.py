"""Voice clips recorded via the STT listener's Journal hotkey, before they
become an entry.

Deliberately NOT `backend.ai.background.run_bg`. That is a single FIFO worker
shared by journal polish, journal metadata, attachment transcription, food
structuring, workout parsing and learning-attempt grading — all triggered by
something the user did seconds ago. Processing a draft is three sequential CPU
transcriptions plus an LLM call, more like backend/research/worker.py's
minutes-long jobs than a quick polish pass; putting it on the shared queue
would head-of-line block every one of those flows. So: its own single-worker
executor, same shape as backend/ai/background.py's, just not shared with it.

Files live under ./data/journal_drafts/<draft_id>/ (JOURNAL_DRAFTS_ROOT) — a
separate root from backend/journal/storage.py's journal_attachments layout,
since a draft has no entry_id yet. Once a draft resolves into an entry, its
audio file is moved (not copied) into that attachment layout.
"""
import json
import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from ulid import ULID

from backend.db.connection import build_update, get_db, row_to_dict
from backend.storage import IdScopedStorage
from backend.journal import storage as attachment_storage
from backend.ai.journal import merge_voice_draft, generate_journal_metadata, PolishUnavailable
from backend.tags import tags_json

logger = logging.getLogger(__name__)

# Same ceiling as journal attachment audio uploads (backend/routes/journal.py's
# MAX_AUDIO_BYTES) — generous enough to never be a real limit, just a stop
# against a mis-picked file filling the disk.
MAX_DRAFT_BYTES = 100 * 1024 * 1024

# The backend list moved to backend/routes/stt.py when every dictation
# surface started using it, not just this pipeline. Imported lazily inside
# _process_draft_inner for the same reason stt is: it drags in numpy/torch.
#
# _pick_primary moved with it — the "whose text becomes raw_content" rule is
# the same wherever a clip is transcribed by more than one model.

_storage = IdScopedStorage('JOURNAL_DRAFTS_ROOT', './data/journal_drafts')
resolve_stored_path = _storage.resolve_stored_path

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='voice-draft')
_pending: set = set()
_pending_lock = threading.Lock()


def _run_bg(fn) -> None:
    future = _executor.submit(fn)
    with _pending_lock:
        _pending.add(future)
    future.add_done_callback(_forget)


def _forget(future) -> None:
    with _pending_lock:
        _pending.discard(future)


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until queued draft processing drains. True if it did, False on
    timeout. For tests and shutdown — mirrors backend/ai/background.py's
    wait_idle, for this feature's own executor (see module docstring)."""
    with _pending_lock:
        pending = list(_pending)
    if not pending:
        return True
    return not wait(pending, timeout=timeout).not_done


_DRAFT_COLS = 'id, path, mime, size, status, error, candidates, entry_id, created_at, completed_at'


def _load_draft(draft_id: str):
    return get_db().execute(
        f'SELECT {_DRAFT_COLS} FROM journal_voice_drafts WHERE id=?', (draft_id,)
    ).fetchone()


def _draft_dict(row) -> dict:
    d = row_to_dict(row)
    d['candidates'] = json.loads(d['candidates']) if d.get('candidates') else []
    d.pop('path', None)
    d['url'] = f"/api/journal/voice-drafts/{row['id']}/file"
    return d


def _draft_path(draft_id: str, ext: str):
    d = _storage.dir(draft_id)
    if d is None:
        return None
    return d / f"audio.{ext.lower().lstrip('.')}"


def _notify(draft_id: str, entry_id: str | None = None) -> None:
    # Deferred: backend/routes/journal.py imports this module at load time to
    # call create_draft/list_drafts/etc, so the reverse import has to happen
    # at call time or the two modules can't both import each other.
    from backend.routes.journal import _notify_subscribers
    _notify_subscribers(entry_id or draft_id)


def create_draft(draft_id: str, file) -> tuple[dict | None, tuple[str, int] | None]:
    """Save an uploaded voice clip as a new draft and kick off processing.

    `file` is a Werkzeug FileStorage. Mirrors _store_attachment's and
    create_recording_entry's idempotency: the listener re-POSTs the same clip
    on every retry until it gets an ack, so a draft id already on file is a
    no-op — checked before the upload is read, so a replay doesn't stream
    megabytes to disk to discover it was already there.

    Returns (draft_dict, None) on success/replay, or (None, (error, status)).
    """
    existing = _load_draft(draft_id)
    if existing is not None:
        return _draft_dict(existing), None

    # `resolve_upload` never refuses any more — anything it doesn't recognise is
    # stored as `kind='file'` for the composer's attach-a-file button. A voice
    # draft is not that: this endpoint takes speech and nothing else, so the
    # kind is checked here rather than relied on to be None.
    ext, kind = attachment_storage.resolve_upload(file.mimetype, file.filename)
    if kind != 'audio':
        return None, ('Unsupported file type — audio only', 400)

    path = _draft_path(draft_id, ext)
    if path is None:
        return None, ('Unsupported file type — audio only', 400)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Streamed to disk rather than read() into memory, matching every other
    # media upload in this app.
    file.save(path)

    size = path.stat().st_size
    if size == 0:
        _storage.delete_dir(draft_id)
        return None, ('file is empty', 400)
    if size > MAX_DRAFT_BYTES:
        _storage.delete_dir(draft_id)
        return None, ('file is too large', 413)

    db = get_db()
    try:
        cur = db.execute(
            "INSERT OR IGNORE INTO journal_voice_drafts"
            "(id, path, mime, size, status, created_at)"
            " VALUES (?,?,?,?,'processing',?)",
            (draft_id, str(path), file.mimetype or None, size, int(time.time())),
        )
        db.commit()
    except Exception:
        _storage.delete_dir(draft_id)
        raise
    if cur.rowcount == 0:
        # Lost a race against another replay of the same id.
        return _draft_dict(_load_draft(draft_id)), None

    _run_bg(lambda: _process_draft(draft_id))
    return _draft_dict(_load_draft(draft_id)), None


def list_drafts() -> list[dict]:
    """Drafts still worth showing in the dropdown — a 'done' draft has already
    become a normal journal entry and is visible in the feed instead."""
    rows = get_db().execute(
        f"SELECT {_DRAFT_COLS} FROM journal_voice_drafts WHERE status != 'done'"
        ' ORDER BY created_at DESC LIMIT 50'
    ).fetchall()
    return [_draft_dict(r) for r in rows]


def delete_draft(draft_id: str) -> bool:
    """Discard a draft that hasn't been promoted to an entry yet. False if not
    found or already promoted — deleting the row then would orphan the live
    entry's attachment reference, so it's left alone as history instead."""
    row = _load_draft(draft_id)
    if row is None or row['entry_id']:
        return False
    get_db().execute('DELETE FROM journal_voice_drafts WHERE id=?', (draft_id,))
    get_db().commit()
    _storage.delete_dir(draft_id)
    return True


def retry_draft(draft_id: str) -> bool:
    row = _load_draft(draft_id)
    if row is None or row['status'] != 'error':
        return False
    get_db().execute(
        "UPDATE journal_voice_drafts SET status='processing', error=NULL WHERE id=?",
        (draft_id,),
    )
    get_db().commit()
    _notify(draft_id)
    _run_bg(lambda: _process_draft(draft_id))
    return True


def _memory_context() -> str | None:
    from backend.memory import get_memory
    memory = get_memory()
    if memory and memory.strip():
        return f'Things already known about the user:\n{memory.strip()}'
    return None


def _finish_error(draft_id: str, error: str, results: list[dict]) -> None:
    db = get_db()
    db.execute(
        "UPDATE journal_voice_drafts SET status='error', error=?, candidates=?, completed_at=?"
        ' WHERE id=?',
        (error, json.dumps(results), int(time.time()), draft_id),
    )
    db.commit()
    _notify(draft_id)


def _finish_done(draft_id: str, entry_id: str, results: list[dict]) -> None:
    db = get_db()
    db.execute(
        "UPDATE journal_voice_drafts SET status='done', entry_id=?, candidates=?,"
        " completed_at=?, error=NULL WHERE id=?",
        (entry_id, json.dumps(results), int(time.time()), draft_id),
    )
    db.commit()
    _notify(draft_id, entry_id)


def _create_entry(content: str, raw_content: str) -> str:
    db = get_db()
    now = int(time.time())
    entry_id = str(ULID())
    db.execute(
        'INSERT INTO journal_entries(id, content, raw_content, title, tags, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (entry_id, content, raw_content, None, None, now, now),
    )
    db.commit()
    return entry_id


def _promote_attachment(entry_id: str, draft_id: str, draft_path: str, mime: str | None) -> None:
    """Move the draft's audio file into the entry's attachment storage and
    register it as a journal_attachments row, so the original recording stays
    reachable exactly like any other attachment — a move, not a copy, since
    the draft's storage root has no other owner for this file."""
    src = Path(draft_path)
    ext = src.suffix.lstrip('.') or 'wav'
    attachment_id = str(ULID())
    dest = attachment_storage.attachment_path(attachment_id, ext)
    if dest is None:
        logger.error('Could not resolve attachment path for promoted draft %s', draft_id)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    # The file already moved out — this just clears the now-empty draft dir.
    _storage.delete_dir(draft_id)

    size = dest.stat().st_size
    db = get_db()
    position = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next FROM journal_attachments WHERE entry_id=?',
        (entry_id,),
    ).fetchone()['next']
    db.execute(
        'INSERT INTO journal_attachments'
        "(id, entry_id, kind, name, path, mime, size, position, transcript_status,"
        " description_status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,'idle','idle',?)",
        (attachment_id, entry_id, 'audio', 'Recording', str(dest), mime, size, position, int(time.time())),
    )
    db.commit()


def _generate_metadata(entry_id: str, content: str) -> None:
    if not content.strip():
        return
    try:
        meta = generate_journal_metadata(content)
    except Exception as e:
        logger.warning('Metadata generation failed for draft-created entry %s: %s', entry_id, e)
        return
    updates: dict = {}
    if meta.get('title'):
        updates['title'] = meta['title']
    if meta.get('tags'):
        updates['tags'] = tags_json(meta['tags'])
    if not updates:
        return
    db = get_db()
    build_update(db, 'journal_entries', updates, 'id=?', (entry_id,))
    db.commit()


def _process_draft(draft_id: str) -> None:
    try:
        _process_draft_inner(draft_id)
    except Exception as e:
        logger.exception('Voice draft processing crashed for %s', draft_id)
        try:
            _finish_error(draft_id, f'Processing failed: {e}', [])
        except Exception:
            logger.exception('Failed to record draft failure for %s', draft_id)


def _process_draft_inner(draft_id: str) -> None:
    row = _load_draft(draft_id)
    if row is None:
        return
    if row['entry_id']:
        # Already promoted — e.g. a crash between creating the entry and
        # marking the draft done. Nothing left to do; a retry must never
        # create a second entry from the same clip.
        _finish_done(draft_id, row['entry_id'], json.loads(row['candidates'] or '[]'))
        return

    path, mime = row['path'], row['mime']

    # Imported here rather than at module scope: the STT module pulls in
    # numpy and (for the local backend) torch, and this module is imported by
    # the journal blueprint, which tests load without paying for that.
    from backend.routes import stt as stt_routes

    content = Path(path).read_bytes()
    filename = Path(path).name
    results = stt_routes.run_multi_backend_transcribe(
        content, filename, None, list(stt_routes.MULTI_BACKENDS)
    )

    candidates = [r for r in results if (r.get('text') or '').strip()]
    if not candidates:
        summary = '; '.join(
            f"{r['backend']}: {r.get('error', 'no speech detected')}" for r in results
        ) or 'All STT backends failed'
        _finish_error(draft_id, summary, results)
        return

    primary = stt_routes.pick_primary(candidates, stt_routes._get_active_stt_backend())
    raw_text = primary['text']

    try:
        content_text = merge_voice_draft(candidates, context=_memory_context())
    except PolishUnavailable as e:
        # Same fallback create_entry uses for a plain single-model transcript:
        # save the raw text now, unpolished. The existing manual Polish button
        # on the entry works normally afterward — raw_content is a plain
        # single transcript either way.
        logger.warning('Voice draft merge unavailable for %s: %s', draft_id, e)
        content_text = raw_text

    entry_id = _create_entry(content_text, raw_text)
    # Recorded immediately, separately from _finish_done below, so a crash
    # between here and _promote_attachment still lets a retry recognize the
    # entry already exists instead of creating a duplicate.
    get_db().execute(
        'UPDATE journal_voice_drafts SET entry_id=? WHERE id=?', (entry_id, draft_id)
    )
    get_db().commit()

    _promote_attachment(entry_id, draft_id, path, mime)
    _generate_metadata(entry_id, content_text)
    _finish_done(draft_id, entry_id, results)
