import json
import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.learning.dedup import embed_answer
from backend.tags import tags_json

bp = Blueprint('learning', __name__, url_prefix='/api/learning')

_TAG_FILTER_SQL = 'EXISTS (SELECT 1 FROM json_each(learning_cards.tags) WHERE json_each.value = ?)'

# Internal columns never sent to the client.
_PRIVATE_CARD_KEYS = ('answerEmbedding', 'claims', 'fsrsState', 'generationContext')


def _card_to_dict(row) -> dict:
    d = row_to_dict(row)
    for key in _PRIVATE_CARD_KEYS:
        d.pop(key, None)
    d['tags'] = json.loads(d['tags']) if d.get('tags') else []
    return d


def _get_card(id):
    return get_db().execute('SELECT * FROM learning_cards WHERE id=?', (id,)).fetchone()


def _insert_cards(cards: list[dict], *, folder_id, tags, source_type, source_id,
                  derived_from, generation_context) -> list[str]:
    """Insert generated cards as pending queue entries; embeddings best-effort."""
    db = get_db()
    now = int(time.time())
    ids = []
    for card in cards:
        id = str(ULID())
        ids.append(id)
        db.execute(
            'INSERT INTO learning_cards'
            ' (id, folder_id, question, answer, state, tags, answer_embedding,'
            '  source_type, source_id, derived_from, generation_context, created_at, updated_at)'
            " VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?)",
            (id, folder_id, card['question'], card['answer'], tags,
             embed_answer(card['answer']), source_type, source_id, derived_from,
             generation_context, now, now),
        )
    db.commit()
    return ids


# ---------------------------------------------------------------- folders

@bp.get('/folders')
def list_folders():
    rows = get_db().execute(
        """
        SELECT f.*, m.name AS evidence_provider_name,
            (SELECT COUNT(*) FROM learning_cards c WHERE c.folder_id=f.id AND c.state='active') AS active_count,
            (SELECT COUNT(*) FROM learning_cards c WHERE c.folder_id=f.id AND c.state='pending') AS pending_count,
            (SELECT COUNT(*) FROM learning_cards c WHERE c.folder_id=f.id AND c.state='active' AND c.due <= ?) AS due_count
        FROM learning_folders f
        LEFT JOIN mcp_servers m ON m.id = f.evidence_provider_id
        ORDER BY f.position, f.created_at
        """,
        (int(time.time()),),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/folders')
def create_folder():
    body = request.json or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    db = get_db()
    now = int(time.time())
    id = str(ULID())
    try:
        db.execute(
            'INSERT INTO learning_folders(id, name, position, created_at, updated_at)'
            ' VALUES (?,?,COALESCE((SELECT MAX(position)+1 FROM learning_folders), 0),?,?)',
            (id, name, now, now),
        )
    except Exception:
        return jsonify({'error': 'A folder with that name already exists'}), 400
    db.commit()
    return jsonify({'id': id}), 201


@bp.patch('/folders/<id>')
def update_folder(id):
    body = request.json or {}
    db = get_db()
    if not db.execute('SELECT 1 FROM learning_folders WHERE id=?', (id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    updates: dict = {}
    if 'name' in body and (body['name'] or '').strip():
        updates['name'] = body['name'].strip()
    if 'position' in body:
        updates['position'] = int(body['position'])
    if 'evidenceProviderId' in body:
        provider_id = body['evidenceProviderId']
        if provider_id is not None and not db.execute(
            'SELECT 1 FROM mcp_servers WHERE id=?', (provider_id,)
        ).fetchone():
            return jsonify({'error': 'Unknown evidence provider'}), 400
        updates['evidence_provider_id'] = provider_id
    if updates:
        updates['updated_at'] = int(time.time())
        set_clause = ', '.join(f'{k}=?' for k in updates)
        db.execute(f'UPDATE learning_folders SET {set_clause} WHERE id=?', [*updates.values(), id])
        db.commit()
    return jsonify({'success': True})


@bp.delete('/folders/<id>')
def delete_folder(id):
    db = get_db()
    db.execute('DELETE FROM learning_folders WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------- generation

@bp.post('/generate')
def generate():
    from backend.ai.learning_generation import generate_cards
    body = request.json or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    cards = generate_cards(text, direction=body.get('direction'))
    if not cards:
        return jsonify({'error': 'No cards could be generated'}), 502
    ids = _insert_cards(
        cards,
        folder_id=body.get('folderId'),
        tags=tags_json(body.get('tags')),
        source_type=body.get('sourceType') or 'braindump',
        source_id=body.get('sourceId'),
        derived_from=body.get('derivedFrom'),
        generation_context=text[:8000],
    )
    return jsonify({'count': len(ids), 'ids': ids})


@bp.post('/generate-from-journal')
def generate_from_journal():
    from backend.ai.learning_generation import generate_cards
    body = request.json or {}
    journal_id = body.get('journalId')
    if not journal_id:
        return jsonify({'error': 'journalId required'}), 400
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (journal_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Journal entry not found'}), 404
    text = f"Title: {row['title']}\n\n{row['content']}" if row['title'] else row['content']
    cards = generate_cards(text)
    if not cards:
        return jsonify({'error': 'No cards could be generated'}), 502
    ids = _insert_cards(
        cards,
        folder_id=body.get('folderId'),
        tags=tags_json(body.get('tags')),
        source_type='journal',
        source_id=journal_id,
        derived_from=None,
        generation_context=text[:8000],
    )
    return jsonify({'count': len(ids), 'ids': ids})


@bp.post('/generate-for-topic')
def generate_for_topic():
    from backend.ai.learning_generation import generate_cards
    body = request.json or {}
    topic = (body.get('topic') or '').strip()
    if not topic:
        return jsonify({'error': 'topic required'}), 400
    related = get_db().execute(
        'SELECT content FROM journal_entries WHERE content LIKE ? LIMIT 3',
        (f'%{topic}%',),
    ).fetchall()
    text = f'Topic to learn: {topic}'
    if related:
        context = '\n\n---\n\n'.join(r['content'] for r in related)
        text += f"\n\nRelated notes from the user's journal:\n{context}"
    cards = generate_cards(text)
    if not cards:
        return jsonify({'error': 'No cards could be generated'}), 502
    ids = _insert_cards(
        cards,
        folder_id=body.get('folderId'),
        tags=tags_json(body.get('tags')),
        source_type='chat',
        source_id=None,
        derived_from=None,
        generation_context=text[:8000],
    )
    return jsonify({'count': len(ids), 'ids': ids})


# ---------------------------------------------------------------- approval queue

@bp.get('/queue')
def list_queue():
    rows = get_db().execute(
        "SELECT * FROM learning_cards WHERE state='pending' ORDER BY created_at"
    ).fetchall()
    return jsonify([_card_to_dict(r) for r in rows])


@bp.post('/queue/<id>/approve')
def approve_card(id):
    row = _get_card(id)
    if not row or row['state'] != 'pending':
        return jsonify({'error': 'Not found'}), 404
    db = get_db()
    now = int(time.time())
    db.execute(
        "UPDATE learning_cards SET state='active', due=?, updated_at=? WHERE id=?",
        (now, now, id),
    )
    db.commit()
    from datetime import datetime, timezone
    return jsonify({
        'status': 'approved',
        'due': datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
    })


@bp.post('/queue/<id>/regenerate')
def regenerate_card(id):
    from backend.ai.learning_generation import regenerate_cards
    body = request.json or {}
    direction = (body.get('direction') or '').strip()
    if not direction:
        return jsonify({'error': 'direction required'}), 400
    row = _get_card(id)
    if not row or row['state'] != 'pending':
        return jsonify({'error': 'Not found'}), 404
    cards = regenerate_cards(
        row['question'], row['answer'], row['generation_context'], direction
    )
    if not cards:
        return jsonify({'error': 'No cards could be generated'}), 502
    db = get_db()
    db.execute('DELETE FROM learning_cards WHERE id=?', (id,))
    db.commit()
    ids = _insert_cards(
        cards,
        folder_id=row['folder_id'],
        tags=row['tags'],
        source_type=row['source_type'],
        source_id=row['source_id'],
        derived_from=row['derived_from'],
        generation_context=row['generation_context'],
    )
    return jsonify({'count': len(ids), 'ids': ids})


@bp.delete('/queue/<id>')
def deny_card(id):
    row = _get_card(id)
    if not row or row['state'] != 'pending':
        return jsonify({'error': 'Not found'}), 404
    db = get_db()
    db.execute('DELETE FROM learning_cards WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------- cards

@bp.get('/cards')
def list_cards():
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    state = request.args.get('state')
    clauses, params = [], []
    if state:
        if state not in ('pending', 'active', 'retired'):
            return jsonify({'error': 'invalid state'}), 400
        clauses.append('state = ?')
        params.append(state)
    else:
        # Default browse is the live deck; pending lives in /queue and
        # retired versions are reachable via ?state=retired / revisions.
        clauses.append("state = 'active'")
    folder_id = request.args.get('folderId')
    if folder_id:
        clauses.append('folder_id = ?')
        params.append(folder_id)
    tag = (request.args.get('tag') or '').strip().lower()
    if tag:
        clauses.append(_TAG_FILTER_SQL)
        params.append(tag)
    rows = get_db().execute(
        f"SELECT * FROM learning_cards WHERE {' AND '.join(clauses)}"
        ' ORDER BY created_at DESC LIMIT ? OFFSET ?',
        [*params, limit, offset],
    ).fetchall()
    return jsonify([_card_to_dict(r) for r in rows])


@bp.get('/cards/<id>')
def get_card(id):
    row = _get_card(id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_card_to_dict(row))


@bp.post('/cards')
def create_card():
    body = request.json or {}
    question = (body.get('question') or '').strip()
    answer = (body.get('answer') or '').strip()
    if not question or not answer:
        return jsonify({'error': 'question and answer required'}), 400
    db = get_db()
    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO learning_cards'
        ' (id, folder_id, question, answer, state, tags, answer_embedding,'
        '  source_type, due, created_at, updated_at)'
        " VALUES (?,?,?,?,'active',?,?,'manual',?,?,?)",
        (id, body.get('folderId'), question, answer, tags_json(body.get('tags')),
         embed_answer(answer), now, now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


@bp.patch('/cards/<id>')
def update_card(id):
    body = request.json or {}
    row = _get_card(id)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    updates: dict = {}
    if 'tags' in body:
        updates['tags'] = tags_json(body['tags'])
    if 'folderId' in body:
        updates['folder_id'] = body['folderId']
    if 'question' in body or 'answer' in body:
        # Content edits on an active card must go through the revise flow so
        # versioning and the FSRS reset gate are never bypassed.
        if row['state'] != 'pending':
            return jsonify({'error': 'Edit the answer via the revise flow'}), 400
        if 'question' in body and (body['question'] or '').strip():
            updates['question'] = body['question'].strip()
        if 'answer' in body and (body['answer'] or '').strip():
            updates['answer'] = body['answer'].strip()
            updates['answer_embedding'] = embed_answer(updates['answer'])
            updates['claims'] = None
    if updates:
        updates['updated_at'] = int(time.time())
        set_clause = ', '.join(f'{k}=?' for k in updates)
        db = get_db()
        db.execute(f'UPDATE learning_cards SET {set_clause} WHERE id=?', [*updates.values(), id])
        db.commit()
    return jsonify({'success': True})


@bp.delete('/cards/<id>')
def delete_card(id):
    # Hard delete; FK actions null children's derived_from/revised_from and
    # cascade the review log.
    db = get_db()
    db.execute('DELETE FROM learning_cards WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


@bp.get('/tags')
def list_tags():
    rows = get_db().execute(
        'SELECT je.value AS name, COUNT(*) AS count'
        ' FROM learning_cards c JOIN json_each(c.tags) je'
        " WHERE c.state != 'retired'"
        ' GROUP BY je.value ORDER BY count DESC, name'
    ).fetchall()
    return jsonify([{'name': r['name'], 'count': r['count']} for r in rows])
