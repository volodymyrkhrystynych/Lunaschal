from flask import Blueprint, jsonify, request, send_file

from backend.db.connection import get_db, row_to_dict
from backend.piano import archive, daily, library, storage
from backend.piano.musicxml import ScoreImportError, normalize_score

bp = Blueprint('piano', __name__, url_prefix='/api/piano')


@bp.get('/today')
def get_today():
    return jsonify(daily.today(get_db()))


@bp.get('/history')
def get_history():
    try:
        limit = min(60, max(1, int(request.args.get('limit', 14))))
    except ValueError:
        return jsonify({'error': 'limit must be a whole number.'}), 400
    return jsonify(daily.history(get_db(), limit=limit))


@bp.patch('/preferences')
def update_preferences():
    body = request.get_json(silent=True) or {}
    try:
        result = daily.update_preferences(
            get_db(), session_minutes=int(body.get('sessionMinutes', 25)),
            skill_level=body.get('skillLevel', ''), jazz_percent=int(body.get('jazzPercent', 50)),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify(result)


@bp.post('/daily/<daily_id>/attempts')
def create_attempt(daily_id):
    try:
        result = daily.record_attempt(get_db(), daily_id, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if result is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(result), 201


@bp.get('/daily/<daily_id>/score')
def get_exercise_score(daily_id):
    score = daily.exercise_score(get_db(), daily_id)
    if score is None:
        return jsonify({'error': 'This exercise has no generated score.'}), 404
    return score, 200, {'Content-Type': 'application/vnd.recordare.musicxml+xml'}


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
    _, row = library.store_piece(score, upload.filename)
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
    if not library.delete_piece(piece_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})


@bp.get('/archive/status')
def archive_status():
    return jsonify(archive.status())


@bp.get('/archive/items')
def archive_items():
    try:
        limit = int(request.args.get('limit', archive.DEFAULT_PAGE_SIZE))
        offset = int(request.args.get('offset', 0))
    except ValueError:
        return jsonify({'error': 'Pagination values must be whole numbers.'}), 400
    return jsonify(
        archive.list_items(
            query=request.args.get('q', ''),
            favorites_only=request.args.get('favorite') == '1',
            limit=limit,
            offset=offset,
        )
    )


@bp.post('/archive/items')
def upload_archive_item():
    upload = request.files.get('file')
    if upload is None:
        return jsonify({'error': 'Choose a file to archive.'}), 400
    try:
        item = archive.store_upload(upload, source_url=request.form.get('sourceUrl'))
    except (ValueError, archive.ArchiveUnavailable) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify(item), 201


@bp.post('/archive/scan')
def scan_archive():
    try:
        return jsonify(archive.scan())
    except archive.ArchiveUnavailable as exc:
        return jsonify({'error': str(exc)}), 400


@bp.patch('/archive/items/<item_id>')
def update_archive_item(item_id):
    body = request.get_json(silent=True) or {}
    if 'favorite' not in body or not isinstance(body['favorite'], bool):
        return jsonify({'error': 'favorite must be true or false.'}), 400
    try:
        item = archive.set_favorite(item_id, body['favorite'])
    except (ScoreImportError, archive.ArchiveUnavailable) as exc:
        return jsonify({'error': str(exc)}), 400
    if item is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(item)


@bp.get('/archive/items/<item_id>/file')
def get_archive_file(item_id):
    try:
        resolved = archive.item_path(item_id)
    except archive.ArchiveUnavailable as exc:
        return jsonify({'error': str(exc)}), 404
    if resolved is None:
        return jsonify({'error': 'Not found'}), 404
    path, row = resolved
    return send_file(
        path,
        mimetype=row['content_type'] or 'application/octet-stream',
        as_attachment=True,
        download_name=row['source_filename'],
        conditional=True,
    )
