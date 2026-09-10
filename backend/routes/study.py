"""Study sources — the left half of the Study desk.

A source is a PDF, an archived web page or a downloaded YouTube video. Uploads
land synchronously; the two URL imports run on a daemon thread with an
in-memory progress registry (backend/study/importer.py) and a persisted
`import_status` that outlives it.
"""
import math
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import build_update, get_db, row_to_dict
from backend.journal_moment import journal_moment
from backend.routes.notebook import notebook_file
from backend.routes.paper import page_image_url
from backend.study import importer, storage

bp = Blueprint('study', __name__, url_prefix='/api/study')

_LIST_COLS = (
    'id, title, kind, source_url, content_type, size_bytes, duration_seconds,'
    ' note_path, paper_id, note_mode, import_status, import_error,'
    ' last_opened_at, position, archive_requested_at, created_at, updated_at'
)

# `file_path` is deliberately absent from _LIST_COLS: a server path is not the
# client's business, and the file is reached through /file instead.


def _attach_progress(source: dict) -> dict:
    progress = importer.get_progress(source['id'])
    if progress:
        source['importProgress'] = progress
    return source


def _attach_availability(source: dict, archive) -> dict:
    """Whether this source's bytes are reachable right now.

    Only archived kinds can answer anything but yes: a video lives on the
    external drive alone, so an unplugged drive means the row is still listed
    and browsable (Piano's model) while the file 404s. Saying so on the row is
    what lets the viewer explain it instead of rendering a dead <video>.
    """
    if source['kind'] not in storage.ARCHIVED_KINDS:
        source['fileAvailable'] = True
        return source
    source['fileAvailable'] = archive.available
    if not archive.available:
        source['fileUnavailableReason'] = archive.reason or 'The archive is unavailable.'
    return source


def _paper_exists(paper_id: str) -> bool:
    return (
        get_db()
        .execute('SELECT 1 FROM papers WHERE id=?', (paper_id,))
        .fetchone()
        is not None
    )


def _archive_state():
    return storage.archive_location_state(get_db())


# A source filed for the Journal stays in the library until the next 4am
# boundary passes, then moves -- the same lazy rule papers follow, computed off
# backend.day_boundary so no scheduler is needed and it survives restarts.

def _cutoff_4am(now_ts: int) -> int:
    return day_bounds(day_key_for(now_ts))[0]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# A note bigger than this is not a study note any more, and the Journal feed
# renders every card it is handed. Truncated rather than dropped: the first
# quarter-megabyte is still the note.
NOTE_BYTE_CAP = 256 * 1024


def _read_note(note_path: str | None) -> tuple[str | None, bool]:
    """The Notebook file's text, and whether the cap cut it short."""
    if not note_path:
        return None, False
    path = notebook_file(note_path)
    if path is None or not path.is_file():
        return None, False
    try:
        raw = path.read_bytes()
    except OSError:
        return None, False
    truncated = len(raw) > NOTE_BYTE_CAP
    return raw[:NOTE_BYTE_CAP].decode('utf-8', 'replace'), truncated


def _note_mtime(note_path: str | None) -> int | None:
    if not note_path:
        return None
    path = notebook_file(note_path)
    if path is None or not path.is_file():
        return None
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return None


def _last_worked_on(row, note_path: str | None) -> int | None:
    """When this source was last *worked on*, for the Journal's timestamp.

    Reading is not working: `study_sources.updated_at` is bumped by `touch` on
    every desk open, so it would put the card at the moment you sat down rather
    than the moment you stopped. What counts is ink in the bound paper and
    keystrokes in the Notebook file -- the two places studying leaves a mark.
    `last_opened_at` is the fallback for a source that has neither, such as a
    video simply watched through; it is the start of that sitting, which is the
    best the record holds.
    """
    marks = [t for t in (row['paper_content_updated_at'], _note_mtime(note_path)) if t]
    if marks:
        return max(marks)
    return row['last_opened_at']


# Module-level so tests can monkeypatch them to the synchronous functions —
# the indirection backend/tests/test_fanfic_import.py's fixture relies on.
def _start_web_import_bg(source_id: str, url: str) -> None:
    threading.Thread(
        target=importer.import_web, args=(source_id, url), daemon=True
    ).start()


def _start_youtube_import_bg(source_id: str, url: str) -> None:
    threading.Thread(
        target=importer.import_youtube, args=(source_id, url), daemon=True
    ).start()


def _public_source(row, archive) -> dict:
    source = _attach_availability(_attach_progress(row_to_dict(row)), archive)
    # The flag reaches the client as a boolean and nothing else. The raw column
    # is not in TIMESTAMP_COLS, so leaving it in would put a bare unix int on
    # every row beside an ISO string that means the same thing -- and the
    # client has no use for the moment, only for the state.
    source.pop('archiveRequestedAt', None)
    source['pendingArchive'] = row['archive_requested_at'] is not None
    return source


@bp.get('/sources')
def list_sources():
    # Sources that have moved into the Journal are gone from here, exactly as a
    # moved paper is gone from the Paper explorer; one flagged since the last
    # 4am stays, marked pending. The two predicates are complements, so a
    # source is in exactly one of the library and the feed at any instant.
    rows = get_db().execute(
        f'SELECT {_LIST_COLS} FROM study_sources'
        ' WHERE archive_requested_at IS NULL OR archive_requested_at >= ?'
        ' ORDER BY created_at DESC',
        (_cutoff_4am(int(time.time())),),
    ).fetchall()
    # Resolved once for the whole listing rather than per row: it reads the
    # settings table and stats the drive.
    archive = _archive_state()
    return jsonify([_public_source(row, archive) for row in rows])


@bp.get('/journal')
def journal_study_sources():
    """Sources that have moved into the Journal, newest first.

    One card carries the whole sitting: the source itself, the pages of the
    paper it was written on, and the text of its Notebook note. They are one
    thing in the day's record -- an article read and the page of notes taken
    beside it are not two events -- which is also why a bound paper gets no
    card of its own (see journal_papers in backend/routes/paper.py).

    Timestamped at the last time it was worked on rather than at the flag; the
    day is still the day it was filed under. See backend/journal_moment.py.
    """
    db = get_db()
    cutoff = _cutoff_4am(int(time.time()))
    rows = db.execute(
        'SELECT s.id, s.title, s.kind, s.source_url, s.duration_seconds,'
        ' s.note_path, s.paper_id, s.last_opened_at, s.archive_requested_at,'
        ' p.content_updated_at AS paper_content_updated_at'
        ' FROM study_sources s LEFT JOIN papers p ON p.id = s.paper_id'
        ' WHERE s.archive_requested_at IS NOT NULL AND s.archive_requested_at < ?',
        (cutoff,),
    ).fetchall()
    archive = _archive_state()
    result = []
    for row in rows:
        pages = []
        if row['paper_id']:
            pages = db.execute(
                'SELECT id, image_path, updated_at FROM paper_pages'
                ' WHERE paper_id=? ORDER BY position ASC',
                (row['paper_id'],),
            ).fetchall()
        note, note_truncated = _read_note(row['note_path'])
        at = journal_moment(
            _last_worked_on(row, row['note_path']), row['archive_requested_at']
        )
        card = {
            'id': row['id'],
            'title': row['title'],
            'kind': row['kind'],
            'sourceUrl': row['source_url'],
            'durationSeconds': row['duration_seconds'],
            'journalDate': day_key_for(row['archive_requested_at']),
            'archivedAt': _iso(at),
            'fileUrl': f"/api/study/sources/{row['id']}/file",
            'notePath': row['note_path'],
            'note': note,
            'noteTruncated': note_truncated,
            # page_image_url, not a second copy of the rule: the `?v=updated_at`
            # cache-bust is what keeps a redrawn page from showing stale ink.
            'pages': [
                {'id': pg['id'], 'imageUrl': page_image_url(pg)} for pg in pages
            ],
            '_at': at,
        }
        # A video on an unplugged drive still gets its card -- listed and
        # unreachable, the way viewerKindFor already treats it.
        _attach_availability(card, archive)
        result.append(card)
    # Sorted here rather than in SQL, for the reason journal_papers gives.
    result.sort(key=lambda r: r['_at'], reverse=True)
    for r in result:
        del r['_at']
    return jsonify(result)


@bp.get('/sources/<source_id>')
def get_source(source_id):
    row = get_db().execute(
        f'SELECT {_LIST_COLS} FROM study_sources WHERE id = ?', (source_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_public_source(row, _archive_state()))


@bp.get('/sources/<source_id>/status')
def import_status(source_id):
    return jsonify(importer.get_progress(source_id) or {'done': True})


def _insert(source_id: str, kind: str, title: str, url: str | None, status: str) -> None:
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO study_sources(id, title, kind, source_url, import_status,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (source_id, title, kind, url, status, now, now),
    )
    db.commit()


@bp.post('/sources/pdf')
def upload_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'Missing file'}), 400
    upload = request.files['file']
    filename = upload.filename or ''
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext != 'pdf':
        return jsonify({'error': 'Unsupported file type — upload a .pdf'}), 400

    data = upload.read()
    if not data:
        return jsonify({'error': 'Empty file'}), 400

    source_id = str(ULID())
    title = filename.rsplit('.', 1)[0].strip() or 'Untitled'
    _insert(source_id, 'pdf', title, None, 'ready')

    db = get_db()
    try:
        path = storage.source_file_path(source_id, 'book', 'pdf', 'pdf')
        if path is None:
            raise ValueError('could not build a storage path')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        db.execute(
            'UPDATE study_sources SET file_path=?, content_type=?, size_bytes=?'
            ' WHERE id=?',
            (str(path), 'application/pdf', len(data), source_id),
        )
        db.commit()
    except Exception as e:  # noqa: BLE001 — leave nothing half-created behind
        db.execute('DELETE FROM study_sources WHERE id=?', (source_id,))
        db.commit()
        storage.delete_source_dir(source_id)
        return jsonify({'error': f'Could not store {filename}: {e}'}), 422

    row = db.execute(
        f'SELECT {_LIST_COLS} FROM study_sources WHERE id=?', (source_id,)
    ).fetchone()
    return jsonify({'id': source_id, 'source': row_to_dict(row)}), 201


def _url_import(kind: str, starter):
    url = ((request.json or {}).get('url') or '').strip()
    if not url:
        return jsonify({'error': 'Missing url'}), 400

    source_id = str(ULID())
    # The URL stands in as the title until the import learns the real one. A
    # status string ("Importing…") would be worse: the row already says that in
    # its subtitle, and a failed import would be left titled with a stage it
    # never got past.
    _insert(source_id, kind, url, url, 'importing')
    importer.start_progress(source_id, 'queued')
    starter(source_id, url)

    row = get_db().execute(
        f'SELECT {_LIST_COLS} FROM study_sources WHERE id=?', (source_id,)
    ).fetchone()
    return jsonify({'id': source_id, 'source': _attach_progress(row_to_dict(row))}), 202


@bp.post('/sources/web')
def import_web():
    return _url_import('web', _start_web_import_bg)


@bp.post('/sources/youtube')
def import_youtube():
    return _url_import('youtube', _start_youtube_import_bg)


def _clean_position(value) -> float | None:
    """A page number or a timestamp in seconds, or None to forget it.

    Never negative, and never NaN/inf — a stored infinity would make the
    restore seek somewhere the media cannot go and read as a broken file.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, number)


@bp.patch('/sources/<source_id>')
def update_source(source_id):
    body = request.json or {}
    updates = {}
    if 'title' in body:
        updates['title'] = (body.get('title') or '').strip()
    if 'notePath' in body:
        # An empty string unbinds the note rather than storing '' — a source
        # with no note and a source bound to the empty path are the same thing.
        updates['note_path'] = (body.get('notePath') or '').strip() or None
    if body.get('touch'):
        updates['last_opened_at'] = int(time.time())
    if 'position' in body:
        updates['position'] = _clean_position(body.get('position'))
    if 'paperId' in body:
        # Same shape as notePath above: '' unbinds rather than storing ''.
        paper_id = (body.get('paperId') or '').strip() or None
        if paper_id and not _paper_exists(paper_id):
            # The column carries a real foreign key, so an unknown id would
            # otherwise surface as an IntegrityError and a 500. The client
            # creates the paper first and binds it second; a failure between
            # those two is a 400 it can retry, not a crash.
            return jsonify({'error': 'No such paper'}), 400
        updates['paper_id'] = paper_id
    if 'noteMode' in body:
        mode = (body.get('noteMode') or '').strip()
        if mode not in ('note', 'paper'):
            return jsonify({'error': 'Unknown note mode'}), 400
        updates['note_mode'] = mode
    if 'archiveRequested' in body:
        # Reversible until the next 4am carries it out of the library, the
        # same as a paper's flag.
        updates['archive_requested_at'] = (
            int(time.time()) if body['archiveRequested'] else None
        )
    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400

    # A position or note-mode write deliberately does not touch `updated_at`.
    # Where you are in a document is not an edit to it — and the position write
    # fires every few seconds of scrolling or playback — while which half of
    # the desk you had open is a view preference, flipped every time you glance
    # at the other pane. Anything ordering by "recently changed" would
    # otherwise be reordered by reading. Binding a paper *is* an edit, and is
    # not on this list, exactly as binding a note is not.
    if set(updates) - {'position', 'note_mode'}:
        updates['updated_at'] = int(time.time())
    db = get_db()
    build_update(db, 'study_sources', updates, 'id = ?', (source_id,))
    db.commit()
    row = db.execute(
        f'SELECT {_LIST_COLS} FROM study_sources WHERE id=?', (source_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_public_source(row, _archive_state()))


@bp.delete('/sources/<source_id>')
def delete_source(source_id):
    db = get_db()
    row = db.execute('SELECT id FROM study_sources WHERE id=?', (source_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    # Drop it from the registry first: an in-flight import reads its absence as
    # cancellation and stops writing to a row that is about to disappear.
    importer.cancel_progress(source_id)
    db.execute('DELETE FROM study_sources WHERE id=?', (source_id,))
    db.commit()
    storage.delete_source_dir(source_id)
    # A video's bytes are on the drive, not under STUDY_ROOT. With the drive
    # unplugged this is a no-op and the directory is orphaned — deleting the
    # row is still the right answer, since refusing to delete a library entry
    # because a disk is elsewhere would be worse than a stray folder.
    storage.delete_archived_dir(source_id, db)
    return jsonify({'success': True})


@bp.get('/sources/<source_id>/file')
def serve_file(source_id):
    db = get_db()
    row = db.execute(
        'SELECT file_path, content_type FROM study_sources WHERE id=?', (source_id,)
    ).fetchone()
    if row is None or not row['file_path']:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['file_path'], db)
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    # conditional=True is load-bearing for video: it is what answers Range
    # requests, and without it seeking a downloaded lecture does nothing.
    return send_file(
        path,
        mimetype=row['content_type'] or storage.mimetype_for(path),
        conditional=True,
    )
