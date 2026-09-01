import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.piano import storage
from backend.piano.musicxml import ScoreImportError, normalize_score, score_metadata

bp = Blueprint('piano', __name__, url_prefix='/api/piano')


@bp.get('/pieces')
def list_pieces():
    rows = get_db().execute(
        'SELECT id, title, composer, source_filename, created_at, updated_at '
        'FROM piano_pieces ORDER BY updated_at DESC'
    ).fetchall()
    return jsonify([row_to_dict(row) for row in rows])


@bp.post('/pieces')
def import_piece():
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return jsonify({'error': 'Choose a MusicXML score.'}), 400
    try:
        score = normalize_score(upload.read(), upload.filename)
    except ScoreImportError as exc:
        return jsonify({'error': str(exc)}), 400
    fallback = upload.filename.rsplit('.', 1)[0].strip() or 'Untitled score'
    title, composer = score_metadata(score, fallback)
    piece_id = str(ULID())
    directory = storage.piano_dir(piece_id)
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / 'score.musicxml'
    try:
        path.write_bytes(score)
        now = int(time.time())
        db = get_db()
        db.execute(
            'INSERT INTO piano_pieces '
            '(id,title,composer,source_filename,score_path,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?)',
            (piece_id, title, composer, upload.filename, str(path), now, now),
        )
        db.commit()
    except Exception:
        storage.delete_piano_dir(piece_id)
        raise
    row = db.execute('SELECT * FROM piano_pieces WHERE id=?', (piece_id,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@bp.get('/pieces/<piece_id>/score')
def get_score(piece_id):
    row = get_db().execute(
        'SELECT score_path FROM piano_pieces WHERE id=?', (piece_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['score_path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Score file is missing'}), 404
    return send_file(path, mimetype='application/vnd.recordare.musicxml+xml', conditional=True)


@bp.delete('/pieces/<piece_id>')
def delete_piece(piece_id):
    db = get_db()
    cursor = db.execute('DELETE FROM piano_pieces WHERE id=?', (piece_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    storage.delete_piano_dir(piece_id)
    return jsonify({'ok': True})
