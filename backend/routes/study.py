"""Study sources — the left half of the Study desk.

A source is a PDF, an archived web page or a downloaded YouTube video. Uploads
land synchronously; the two URL imports run on a daemon thread with an
in-memory progress registry (backend/study/importer.py) and a persisted
`import_status` that outlives it.
"""
import threading
import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import build_update, get_db, row_to_dict
from backend.study import importer, storage

bp = Blueprint('study', __name__, url_prefix='/api/study')

_LIST_COLS = (
    'id, title, kind, source_url, content_type, size_bytes, duration_seconds,'
    ' note_path, import_status, import_error, last_opened_at, created_at, updated_at'
)

# `file_path` is deliberately absent from _LIST_COLS: a server path is not the
# client's business, and the file is reached through /file instead.


def _attach_progress(source: dict) -> dict:
    progress = importer.get_progress(source['id'])
    if progress:
        source['importProgress'] = progress
    return source


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


@bp.get('/sources')
def list_sources():
    rows = get_db().execute(
        f'SELECT {_LIST_COLS} FROM study_sources ORDER BY created_at DESC'
    ).fetchall()
    return jsonify([_attach_progress(row_to_dict(row)) for row in rows])


@bp.get('/sources/<source_id>')
def get_source(source_id):
    row = get_db().execute(
        f'SELECT {_LIST_COLS} FROM study_sources WHERE id = ?', (source_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_attach_progress(row_to_dict(row)))


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
        path = storage.source_file_path(source_id, 'book', 'pdf')
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
    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400

    updates['updated_at'] = int(time.time())
    db = get_db()
    build_update(db, 'study_sources', updates, 'id = ?', (source_id,))
    db.commit()
    row = db.execute(
        f'SELECT {_LIST_COLS} FROM study_sources WHERE id=?', (source_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


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
    return jsonify({'success': True})


@bp.get('/sources/<source_id>/file')
def serve_file(source_id):
    row = get_db().execute(
        'SELECT file_path, content_type FROM study_sources WHERE id=?', (source_id,)
    ).fetchone()
    if row is None or not row['file_path']:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['file_path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    # conditional=True is load-bearing for video: it is what answers Range
    # requests, and without it seeking a downloaded lecture does nothing.
    return send_file(
        path,
        mimetype=row['content_type'] or storage.mimetype_for(path),
        conditional=True,
    )
