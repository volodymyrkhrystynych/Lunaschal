import time
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend.db.connection import get_db, row_to_dict

bp = Blueprint('flashcard', __name__, url_prefix='/api/flashcards')


def _sm2(interval: int, repetitions: int, efactor: float, grade: int) -> tuple[int, int, float]:
    if grade >= 3:
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = round(interval * efactor)
        new_reps = repetitions + 1
    else:
        new_interval = 1
        new_reps = 0
    new_ef = max(1.3, efactor + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    return new_interval, new_reps, new_ef


@bp.get('')
def list_cards():
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    rows = get_db().execute(
        'SELECT * FROM flashcards ORDER BY created_at DESC LIMIT ? OFFSET ?', (limit, offset)
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/due')
def get_due():
    now = int(time.time())
    rows = get_db().execute(
        'SELECT * FROM flashcards WHERE next_review <= ? ORDER BY next_review LIMIT 20', (now,)
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/stats')
def get_stats():
    db = get_db()
    now = int(time.time())
    total = db.execute('SELECT COUNT(*) FROM flashcards').fetchone()[0]
    due = db.execute('SELECT COUNT(*) FROM flashcards WHERE next_review <= ?', (now,)).fetchone()[0]
    mastered = db.execute('SELECT COUNT(*) FROM flashcards WHERE interval >= 21').fetchone()[0]
    return jsonify({'total': total, 'due': due, 'mastered': mastered, 'learning': total - mastered})


@bp.get('/by-source/<source_id>')
def get_by_source(source_id):
    rows = get_db().execute(
        'SELECT * FROM flashcards WHERE source_id=? ORDER BY created_at DESC', (source_id,)
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/<id>')
def get_card(id):
    row = get_db().execute('SELECT * FROM flashcards WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.post('')
def create_card():
    body = request.json or {}
    if not body.get('front') or not body.get('back'):
        return jsonify({'error': 'front and back required'}), 400
    now = int(time.time())
    id = str(ULID())
    get_db().execute(
        'INSERT INTO flashcards(id, front, back, source_id, easiness, interval, repetitions, next_review, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (id, body['front'], body['back'], body.get('sourceId'), 2.5, 0, 0, now, now),
    )
    get_db().commit()
    return jsonify({'id': id}), 201


@bp.post('/<id>/review')
def review_card(id):
    body = request.json or {}
    grade = int(body.get('grade', 0))
    db = get_db()
    row = db.execute('SELECT * FROM flashcards WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    new_interval, new_reps, new_ef = _sm2(
        row['interval'] or 0, row['repetitions'] or 0, row['easiness'] or 2.5, grade
    )
    next_review = int((datetime.now(timezone.utc) + timedelta(days=new_interval)).timestamp())
    db.execute(
        'UPDATE flashcards SET easiness=?, interval=?, repetitions=?, next_review=? WHERE id=?',
        (new_ef, new_interval, new_reps, next_review, id),
    )
    db.commit()
    from datetime import datetime as dt
    return jsonify({
        'nextReview': dt.fromtimestamp(next_review, tz=timezone.utc).isoformat(),
        'interval': new_interval,
    })


@bp.patch('/<id>')
def update_card(id):
    body = request.json or {}
    updates: dict = {}
    if 'front' in body:
        updates['front'] = body['front']
    if 'back' in body:
        updates['back'] = body['back']
    if not updates:
        return jsonify({'success': True})
    set_clause = ', '.join(f'{k}=?' for k in updates)
    get_db().execute(f'UPDATE flashcards SET {set_clause} WHERE id=?', [*updates.values(), id])
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_card(id):
    get_db().execute('DELETE FROM flashcards WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.post('/generate-from-journal')
def generate_from_journal():
    from backend.ai.flashcards import generate_flashcards_from_content
    body = request.json or {}
    journal_id = body.get('journalId')
    if not journal_id:
        return jsonify({'error': 'journalId required'}), 400
    db = get_db()
    row = db.execute('SELECT * FROM journal_entries WHERE id=?', (journal_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Journal entry not found'}), 404
    cards = generate_flashcards_from_content(row['content'], row['title'])
    now = int(time.time())
    ids = []
    for card in cards:
        id = str(ULID())
        ids.append(id)
        db.execute(
            'INSERT INTO flashcards(id, front, back, source_id, easiness, interval, repetitions, next_review, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (id, card['front'], card['back'], journal_id, 2.5, 0, 0, now, now),
        )
    db.commit()
    return jsonify({'count': len(ids), 'ids': ids})


@bp.post('/generate-for-topic')
def generate_for_topic():
    from backend.ai.flashcards import generate_flashcards_for_topic
    body = request.json or {}
    topic = body.get('topic', '').strip()
    if not topic:
        return jsonify({'error': 'topic required'}), 400
    db = get_db()
    related = db.execute(
        "SELECT content FROM journal_entries WHERE content LIKE ? LIMIT 3",
        (f'%{topic}%',),
    ).fetchall()
    context = '\n\n---\n\n'.join(r['content'] for r in related) if related else None
    cards = generate_flashcards_for_topic(topic, context)
    now = int(time.time())
    ids = []
    for card in cards:
        id = str(ULID())
        ids.append(id)
        db.execute(
            'INSERT INTO flashcards(id, front, back, source_id, easiness, interval, repetitions, next_review, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (id, card['front'], card['back'], None, 2.5, 0, 0, now, now),
        )
    db.commit()
    return jsonify({'count': len(ids), 'ids': ids})
