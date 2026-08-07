import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, mapping_to_dict, row_to_dict
from backend.practice.grading import rating_label
from backend.practice.queue import DEFAULT_SIZE, build_session
from backend.practice.snippets import LANGUAGES, SNIPPETS, SNIPPETS_BY_ID

bp = Blueprint('practice', __name__, url_prefix='/api/practice')


def _load_progress(db) -> dict[str, dict]:
    rows = db.execute('SELECT * FROM practice_progress').fetchall()
    return {r['snippet_id']: dict(r) for r in rows}


def _filter_snippets(language: str | None, category: str | None) -> list[dict]:
    return [
        s
        for s in SNIPPETS
        if (language is None or s['language'] == language)
        and (category is None or s['category'] == category)
    ]


@bp.get('/session')
def session():
    language = request.args.get('language') or None
    category = request.args.get('category') or None
    size = min(int(request.args.get('size', DEFAULT_SIZE)), 50)
    db = get_db()
    progress = _load_progress(db)
    ids = build_session(
        SNIPPETS, progress, size=size, language=language, category=category
    )
    return jsonify([SNIPPETS_BY_ID[i] for i in ids])


@bp.get('/snippets')
def list_snippets():
    language = request.args.get('language') or None
    category = request.args.get('category') or None
    db = get_db()
    progress = _load_progress(db)
    return jsonify(
        [
            {
                **s,
                'progress': mapping_to_dict(progress[s['id']]) if s['id'] in progress else None,
            }
            for s in _filter_snippets(language, category)
        ]
    )


@bp.post('/attempts')
def submit_attempt():
    body = request.get_json(force=True) or {}
    snippet_id = body.get('snippetId')
    if snippet_id not in SNIPPETS_BY_ID:
        return jsonify({'error': 'Unknown snippet'}), 400

    wpm = float(body.get('wpm', 0))
    accuracy = float(body.get('accuracy', 0))
    error_count = int(body.get('errorCount', 0))

    db = get_db()
    now = int(time.time())
    existing = db.execute(
        'SELECT attempts_count, best_wpm, best_accuracy FROM practice_progress WHERE snippet_id = ?',
        (snippet_id,),
    ).fetchone()
    attempts_count = (existing['attempts_count'] if existing else 0) + 1
    best_wpm = max(wpm, existing['best_wpm'] or 0.0) if existing else wpm
    best_accuracy = max(accuracy, existing['best_accuracy'] or 0.0) if existing else accuracy

    db.execute(
        '''INSERT INTO practice_progress
               (snippet_id, attempts_count, last_wpm, last_accuracy, best_wpm, best_accuracy,
                last_practiced_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snippet_id) DO UPDATE SET
               attempts_count = excluded.attempts_count,
               last_wpm = excluded.last_wpm,
               last_accuracy = excluded.last_accuracy,
               best_wpm = excluded.best_wpm,
               best_accuracy = excluded.best_accuracy,
               last_practiced_at = excluded.last_practiced_at,
               updated_at = excluded.updated_at''',
        (snippet_id, attempts_count, wpm, accuracy, best_wpm, best_accuracy, now, now),
    )
    db.execute(
        'INSERT INTO practice_attempts (id, snippet_id, wpm, accuracy, error_count, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (str(ULID()), snippet_id, wpm, accuracy, error_count, now),
    )
    db.commit()

    progress_row = db.execute(
        'SELECT * FROM practice_progress WHERE snippet_id = ?', (snippet_id,)
    ).fetchone()
    return jsonify(
        {
            'rating': rating_label(accuracy, wpm),
            'progress': row_to_dict(progress_row),
        }
    )


@bp.get('/stats')
def stats():
    db = get_db()
    totals = db.execute(
        'SELECT COUNT(*) as attempts, AVG(accuracy) as avg_accuracy, AVG(wpm) as avg_wpm '
        'FROM practice_attempts'
    ).fetchone()

    by_language = {}
    for lang in LANGUAGES:
        ids = [s['id'] for s in SNIPPETS if s['language'] == lang]
        placeholders = ','.join('?' * len(ids))
        row = db.execute(
            f'SELECT COUNT(*) as attempts, AVG(accuracy) as avg_accuracy, AVG(wpm) as avg_wpm '
            f'FROM practice_attempts WHERE snippet_id IN ({placeholders})',
            ids,
        ).fetchone()
        by_language[lang] = {
            'attempts': row['attempts'],
            'avgAccuracy': row['avg_accuracy'],
            'avgWpm': row['avg_wpm'],
        }

    return jsonify(
        {
            'totalAttempts': totals['attempts'],
            'avgAccuracy': totals['avg_accuracy'],
            'avgWpm': totals['avg_wpm'],
            'byLanguage': by_language,
        }
    )
