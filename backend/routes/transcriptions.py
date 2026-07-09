from flask import Blueprint, jsonify, request
from backend.db.connection import get_db, row_to_dict

bp = Blueprint('transcriptions', __name__, url_prefix='/api/transcriptions')


@bp.get('')
def list_transcriptions():
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    rows = get_db().execute(
        'SELECT * FROM transcriptions ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (limit, offset),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.delete('/<id>')
def delete_transcription(id):
    db = get_db()
    db.execute('DELETE FROM transcriptions WHERE id = ?', (id,))
    db.commit()
    return jsonify({'success': True})
