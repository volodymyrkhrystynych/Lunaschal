import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from backend.db.connection import get_db, row_to_dict
from backend.routes.files import make_files_blueprint

NOTEBOOK_ROOT_ENV = 'NOTEBOOK_ROOT'
NOTEBOOK_DEFAULT_ROOT = './data/notebook'


def _like_prefix(rel: str) -> str:
    escaped = rel.rstrip('/').replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'{escaped}/%'


def _sync_rename(from_rel: str, to_rel: str) -> None:
    db = get_db()
    now = int(time.time())
    db.execute(
        'UPDATE notebook_review_state SET path=?, updated_at=? WHERE path=?',
        (to_rel, now, from_rel),
    )
    prefix = from_rel.rstrip('/') + '/'
    rows = db.execute(
        'SELECT path FROM notebook_review_state WHERE path LIKE ? ESCAPE \'\\\'',
        (_like_prefix(from_rel),),
    ).fetchall()
    for r in rows:
        new_path = to_rel.rstrip('/') + '/' + r['path'][len(prefix):]
        db.execute(
            'UPDATE notebook_review_state SET path=?, updated_at=? WHERE path=?',
            (new_path, now, r['path']),
        )
    db.commit()


def _sync_delete(rel: str) -> None:
    db = get_db()
    db.execute(
        'DELETE FROM notebook_review_state WHERE path=? OR path LIKE ? ESCAPE \'\\\'',
        (rel, _like_prefix(rel)),
    )
    db.commit()


files_bp = make_files_blueprint(
    'notebook_files',
    '/api/notebook/files',
    NOTEBOOK_ROOT_ENV,
    NOTEBOOK_DEFAULT_ROOT,
    on_rename=_sync_rename,
    on_delete=_sync_delete,
)

bp = Blueprint('notebook_review', __name__, url_prefix='/api/notebook/review')


def _row(path: str):
    return get_db().execute(
        'SELECT * FROM notebook_review_state WHERE path=?', (path,)
    ).fetchone()


@bp.get('/state')
def get_state():
    path = request.args.get('path', '')
    row = _row(path)
    if not row:
        return jsonify({'enabled': False, 'due': None, 'path': path})
    d = row_to_dict(row)
    d['enabled'] = bool(d['enabled'])
    return jsonify(d)


@bp.post('/toggle')
def toggle():
    body = request.json or {}
    path = body.get('path', '')
    enabled = bool(body.get('enabled'))
    if not path:
        return jsonify({'error': 'path required'}), 400
    db = get_db()
    now = int(time.time())
    row = _row(path)
    if row is None:
        db.execute(
            'INSERT INTO notebook_review_state'
            ' (path, enabled, fsrs_state, due, created_at, updated_at)'
            ' VALUES (?,?,NULL,?,?,?)',
            (path, int(enabled), now if enabled else None, now, now),
        )
    else:
        # Re-enabling a previously-reviewed file keeps its schedule; only a
        # never-reviewed row gets due=now stamped on first enable.
        if enabled and row['fsrs_state'] is None and row['due'] is None:
            db.execute(
                'UPDATE notebook_review_state SET enabled=?, due=?, updated_at=? WHERE path=?',
                (int(enabled), now, now, path),
            )
        else:
            db.execute(
                'UPDATE notebook_review_state SET enabled=?, updated_at=? WHERE path=?',
                (int(enabled), now, path),
            )
    db.commit()
    return jsonify({'success': True})


@bp.get('/due')
def get_due():
    rows = get_db().execute(
        "SELECT * FROM notebook_review_state WHERE enabled=1 AND due<=?"
        ' ORDER BY due LIMIT 20',
        (int(time.time()),),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/rate')
def rate():
    from backend.learning import scheduler

    body = request.json or {}
    path = body.get('path', '')
    try:
        rating = int(body.get('rating'))
    except (TypeError, ValueError):
        return jsonify({'error': 'rating must be 1-4'}), 400
    if rating not in (1, 2, 3, 4):
        return jsonify({'error': 'rating must be 1-4'}), 400
    row = _row(path)
    if not row or not row['enabled']:
        return jsonify({'error': 'Not found'}), 404
    new_state, due, _log = scheduler.review(row['fsrs_state'], rating)
    now = int(time.time())
    db = get_db()
    db.execute(
        'UPDATE notebook_review_state SET fsrs_state=?, due=?, updated_at=? WHERE path=?',
        (new_state, due, now, path),
    )
    db.commit()
    return jsonify({'due': datetime.fromtimestamp(due, tz=timezone.utc).isoformat()})
