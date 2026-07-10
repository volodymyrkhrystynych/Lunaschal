from flask import Blueprint, jsonify, send_file

from backend.db.connection import get_db
from backend.newspapers import sync
from backend.newspapers.storage import PAPERS, resolve_stored_path

bp = Blueprint('newspapers', __name__, url_prefix='/api/newspapers')


@bp.post('/sync')
def do_sync():
    return jsonify(sync.sync_today())


@bp.get('/frontpages/<date>')
def frontpages(date):
    db = get_db()
    rows = {
        r['paper']
        for r in db.execute('SELECT paper FROM newspaper_frontpages WHERE date = ?', (date,))
    }
    return jsonify([
        {
            'paper': paper,
            'label': info['label'],
            'date': date,
            'imageUrl': f'/api/newspapers/image/{paper}/{date}' if paper in rows else None,
        }
        for paper, info in PAPERS.items()
    ])


@bp.get('/image/<paper>/<date>')
def serve_image(paper, date):
    db = get_db()
    row = db.execute(
        'SELECT image_path FROM newspaper_frontpages WHERE paper = ? AND date = ?',
        (paper, date),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    path = resolve_stored_path(row['image_path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, max_age=31536000)
