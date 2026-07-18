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


# ---------------------------------------------------------------- MCP registry

def _server_to_dict(row) -> dict:
    d = row_to_dict(row)
    d['args'] = json.loads(d['args']) if d.get('args') else []
    d['env'] = json.loads(d['env']) if d.get('env') else {}
    return d


def _validate_server_body(body: dict, *, partial: bool) -> tuple[dict, str | None]:
    fields: dict = {}
    if 'name' in body or not partial:
        name = (body.get('name') or '').strip()
        if not name:
            return {}, 'name required'
        fields['name'] = name
    if 'transport' in body or not partial:
        transport = body.get('transport') or 'stdio'
        if transport not in ('stdio', 'http'):
            return {}, "transport must be 'stdio' or 'http'"
        fields['transport'] = transport
    if 'command' in body:
        fields['command'] = (body.get('command') or '').strip() or None
    if 'args' in body:
        args = body.get('args') or []
        if not isinstance(args, list):
            return {}, 'args must be a list'
        fields['args'] = json.dumps([str(a) for a in args]) if args else None
    if 'env' in body:
        env = body.get('env') or {}
        if not isinstance(env, dict):
            return {}, 'env must be an object'
        fields['env'] = json.dumps({str(k): str(v) for k, v in env.items()}) if env else None
    if 'url' in body:
        fields['url'] = (body.get('url') or '').strip() or None
    return fields, None


@bp.get('/mcp-servers')
def list_mcp_servers():
    rows = get_db().execute('SELECT * FROM mcp_servers ORDER BY name').fetchall()
    return jsonify([_server_to_dict(r) for r in rows])


@bp.post('/mcp-servers')
def create_mcp_server():
    body = request.json or {}
    fields, err = _validate_server_body(body, partial=False)
    if not err:
        if fields['transport'] == 'stdio' and not fields.get('command'):
            err = 'stdio transport requires command'
        elif fields['transport'] == 'http' and not fields.get('url'):
            err = 'http transport requires url'
    if err:
        return jsonify({'error': err}), 400
    db = get_db()
    now = int(time.time())
    id = str(ULID())
    try:
        db.execute(
            'INSERT INTO mcp_servers(id, name, transport, command, args, env, url, created_at, updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?)',
            (id, fields['name'], fields['transport'], fields.get('command'),
             fields.get('args'), fields.get('env'), fields.get('url'), now, now),
        )
    except Exception:
        return jsonify({'error': 'A server with that name already exists'}), 400
    db.commit()
    return jsonify({'id': id}), 201


@bp.patch('/mcp-servers/<id>')
def update_mcp_server(id):
    db = get_db()
    if not db.execute('SELECT 1 FROM mcp_servers WHERE id=?', (id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    fields, err = _validate_server_body(request.json or {}, partial=True)
    if err:
        return jsonify({'error': err}), 400
    if fields:
        fields['updated_at'] = int(time.time())
        set_clause = ', '.join(f'{k}=?' for k in fields)
        db.execute(f'UPDATE mcp_servers SET {set_clause} WHERE id=?', [*fields.values(), id])
        db.commit()
    return jsonify({'success': True})


@bp.delete('/mcp-servers/<id>')
def delete_mcp_server(id):
    db = get_db()
    db.execute('DELETE FROM mcp_servers WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


@bp.post('/mcp-servers/<id>/test')
def test_mcp_server(id):
    from backend.ai import mcp_client
    row = get_db().execute('SELECT * FROM mcp_servers WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(mcp_client.test_server(row))


# ---------------------------------------------------------------- verification

def _resolve_provider(card_row):
    if not card_row['folder_id']:
        return None
    return get_db().execute(
        'SELECT m.* FROM mcp_servers m'
        ' JOIN learning_folders f ON f.evidence_provider_id = m.id'
        ' WHERE f.id = ?',
        (card_row['folder_id'],),
    ).fetchone()


def _run_verification(row, followup=None, transcript=None):
    from backend.ai import mcp_client
    from backend.ai.learning_verification import build_case
    from backend.ai.llm import ToolCallingUnsupported

    server = _resolve_provider(row)
    if not server:
        # Trust-first: no bound provider means no case — never open-web.
        return jsonify({'status': 'noProvider', 'case': None, 'transcript': []})

    async def worker(session):
        return await build_case(session, row['question'], row['answer'],
                                followup=followup, transcript=transcript)

    try:
        case, out_transcript = mcp_client.run_tool_session(server, worker)
    except ToolCallingUnsupported:
        return jsonify({
            'status': 'providerUnsupported', 'case': None, 'transcript': [],
            'error': 'Verification requires the OpenAI or Ollama provider',
        })
    except Exception as e:
        return jsonify({'error': f'Evidence provider failed: {e}'}), 502

    status = 'notFound' if case['verdict'] == 'notFound' else 'ok'
    return jsonify({'status': status, 'case': case, 'transcript': out_transcript})


@bp.post('/cards/<id>/verify')
def verify_card(id):
    row = _get_card(id)
    if not row or row['state'] != 'active':
        return jsonify({'error': 'Not found'}), 404
    return _run_verification(row)


@bp.post('/cards/<id>/verify/followup')
def verify_followup(id):
    body = request.json or {}
    question = (body.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question required'}), 400
    row = _get_card(id)
    if not row or row['state'] != 'active':
        return jsonify({'error': 'Not found'}), 404
    return _run_verification(row, followup=question,
                             transcript=body.get('transcript') or None)


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
    from backend.learning.dedup import DEDUP_THRESHOLD, find_similar_answer
    body = request.json or {}
    row = _get_card(id)
    if not row or row['state'] != 'pending':
        return jsonify({'error': 'Not found'}), 404
    db = get_db()

    # Duplicate detection is a dismissable hint, never an auto-block: surface
    # the nearest active answer and let the user decide (force re-approves).
    if not body.get('force'):
        embedding = row['answer_embedding']
        if embedding is None:
            embedding = embed_answer(row['answer'])
            if embedding is not None:
                db.execute('UPDATE learning_cards SET answer_embedding=? WHERE id=?',
                           (embedding, id))
                db.commit()
        if embedding is not None:
            match = find_similar_answer(embedding, id)
            if match and match[1] >= DEDUP_THRESHOLD:
                similar, score = match
                return jsonify({
                    'status': 'duplicateHint',
                    'similar': similar,
                    'score': round(score, 3),
                })

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


# ---------------------------------------------------------------- versioning

@bp.post('/cards/<id>/revise')
def revise_card(id):
    import difflib
    from backend.ai.learning_verification import judge_semantic_change
    body = request.json or {}
    new_answer = (body.get('answer') or '').strip()
    trigger_type = body.get('triggerType') or 'manual_edit'
    if not new_answer:
        return jsonify({'error': 'answer required'}), 400
    if trigger_type not in ('manual_edit', 'web_verification'):
        return jsonify({'error': 'invalid triggerType'}), 400
    row = _get_card(id)
    if not row or row['state'] != 'active':
        return jsonify({'error': 'Not found'}), 404

    new_question = (body.get('question') or '').strip() or row['question']
    old_answer = row['answer']
    diff = '\n'.join(difflib.unified_diff(
        old_answer.splitlines(), new_answer.splitlines(),
        fromfile='old', tofile='new', lineterm='',
    ))
    is_semantic = judge_semantic_change(old_answer, new_answer)

    db = get_db()
    now = int(time.time())
    new_id = str(ULID())
    # Semantic change: aggressive relearn (fresh FSRS, due now). Cosmetic:
    # the schedule survives — resetting a well-learned card for a typo fix
    # is pure loss.
    fsrs_state, due = (None, now) if is_semantic else (row['fsrs_state'], row['due'])
    db.execute(
        'INSERT INTO learning_cards'
        ' (id, folder_id, question, answer, state, tags, answer_embedding,'
        '  source_type, source_id, derived_from, revised_from, generation_context,'
        '  fsrs_state, due, created_at, updated_at)'
        " VALUES (?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?)",
        (new_id, row['folder_id'], new_question, new_answer, row['tags'],
         embed_answer(new_answer), row['source_type'], row['source_id'],
         row['derived_from'], id, row['generation_context'],
         fsrs_state, due, now, now),
    )
    db.execute(
        "UPDATE learning_cards SET state='retired', updated_at=? WHERE id=?",
        (now, id),
    )
    sources = body.get('sources')
    db.execute(
        'INSERT INTO learning_revisions'
        ' (id, old_card_id, new_card_id, trigger_type, old_answer, new_answer,'
        '  diff, is_semantic, sources, note, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (str(ULID()), id, new_id, trigger_type, old_answer, new_answer, diff,
         int(is_semantic), json.dumps(sources) if sources else None,
         body.get('note'), now),
    )
    db.commit()
    return jsonify({'newCardId': new_id, 'isSemantic': is_semantic})


@bp.get('/cards/<id>/revisions')
def list_revisions(id):
    """Revision chain for a card, newest first, following revised_from links."""
    db = get_db()
    if not _get_card(id):
        return jsonify({'error': 'Not found'}), 404
    revisions = []
    current, seen = id, set()
    while current and current not in seen:
        seen.add(current)
        row = db.execute(
            'SELECT * FROM learning_revisions WHERE new_card_id=?', (current,)
        ).fetchone()
        if not row:
            break
        d = row_to_dict(row)
        d['sources'] = json.loads(d['sources']) if d.get('sources') else []
        d['isSemantic'] = bool(d['isSemantic'])
        revisions.append(d)
        current = row['old_card_id']
    return jsonify(revisions)


# ---------------------------------------------------------------- review

def _review_filters() -> tuple[str, list]:
    clause, params = '', []
    folder_id = request.args.get('folderId')
    if folder_id:
        clause += ' AND folder_id = ?'
        params.append(folder_id)
    tag = (request.args.get('tag') or '').strip().lower()
    if tag:
        clause += f' AND {_TAG_FILTER_SQL}'
        params.append(tag)
    return clause, params


@bp.get('/due')
def get_due():
    clause, params = _review_filters()
    rows = get_db().execute(
        f"SELECT * FROM learning_cards WHERE state='active' AND due <= ?{clause}"
        ' ORDER BY due LIMIT 20',
        [int(time.time()), *params],
    ).fetchall()
    return jsonify([_card_to_dict(r) for r in rows])


@bp.get('/stats')
def get_stats():
    clause, params = _review_filters()
    now = int(time.time())
    row = get_db().execute(
        f"""
        SELECT
            COALESCE(SUM(state='active'), 0) AS total,
            COALESCE(SUM(state='active' AND due <= ?), 0) AS due,
            COALESCE(SUM(state='pending'), 0) AS pending,
            COALESCE(SUM(state='active' AND json_extract(fsrs_state, '$.stability') >= 21), 0) AS mastered
        FROM learning_cards WHERE state != 'retired'{clause}
        """,
        [now, *params],
    ).fetchone()
    return jsonify({
        'total': row['total'], 'due': row['due'], 'pending': row['pending'],
        'mastered': row['mastered'], 'learning': row['total'] - row['mastered'],
    })


def _card_claims(row) -> list[dict]:
    """Cached claim decomposition; computed at first grade, stored on the card."""
    from backend.ai.learning_grading import decompose_claims
    if row['claims']:
        return json.loads(row['claims'])
    claims = decompose_claims(row['question'], row['answer'])
    db = get_db()
    db.execute('UPDATE learning_cards SET claims=? WHERE id=?', (json.dumps(claims), row['id']))
    db.commit()
    return claims


@bp.post('/cards/<id>/grade')
def grade_card(id):
    from backend.ai import learning_grading
    from backend.learning.dedup import cosine
    body = request.json or {}
    answer = (body.get('answer') or '').strip()
    if not answer:
        return jsonify({'error': 'answer required'}), 400
    row = _get_card(id)
    if not row or row['state'] != 'active':
        return jsonify({'error': 'Not found'}), 404

    if body.get('answerMode') == 'voice':
        from backend.ai.learning_generation import normalize_transcript
        answer = normalize_transcript(answer)

    # Cheap gate: an answer nowhere near the stored one is graded Again
    # without the claim-check LLM call.
    if row['answer_embedding'] is not None:
        sim = cosine(embed_answer(answer), row['answer_embedding'])
        if sim is not None and sim < learning_grading.GATE_LOW:
            coverage = learning_grading.gated_coverage()
            return jsonify({
                'coverage': coverage,
                'suggestedRating': learning_grading.suggest_rating(coverage),
                'normalizedAnswer': answer,
            })

    coverage = learning_grading.check_coverage(_card_claims(row), answer)
    return jsonify({
        'coverage': coverage,
        'suggestedRating': learning_grading.suggest_rating(coverage),
        'normalizedAnswer': answer,
    })


@bp.post('/cards/<id>/review')
def review_card(id):
    from backend.learning import scheduler
    body = request.json or {}
    try:
        rating = int(body.get('rating'))
    except (TypeError, ValueError):
        return jsonify({'error': 'rating must be 1-4'}), 400
    if rating not in (1, 2, 3, 4):
        return jsonify({'error': 'rating must be 1-4'}), 400
    row = _get_card(id)
    if not row or row['state'] != 'active':
        return jsonify({'error': 'Not found'}), 404

    new_state, due, review_log = scheduler.review(row['fsrs_state'], rating)
    now = int(time.time())
    db = get_db()
    db.execute(
        'UPDATE learning_cards SET fsrs_state=?, due=?, updated_at=? WHERE id=?',
        (new_state, due, now, id),
    )
    coverage = body.get('coverage')
    db.execute(
        'INSERT INTO learning_reviews'
        ' (id, card_id, rating, suggested_rating, user_answer, coverage, answer_mode, review_log, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?)',
        (str(ULID()), id, rating, body.get('suggestedRating'), body.get('userAnswer'),
         json.dumps(coverage) if coverage is not None else None,
         body.get('answerMode'), review_log, now),
    )
    db.commit()
    from datetime import datetime, timezone
    return jsonify({
        'due': datetime.fromtimestamp(due, tz=timezone.utc).isoformat(),
        'state': 'active',
    })


@bp.get('/tags')
def list_tags():
    rows = get_db().execute(
        'SELECT je.value AS name, COUNT(*) AS count'
        ' FROM learning_cards c JOIN json_each(c.tags) je'
        " WHERE c.state != 'retired'"
        ' GROUP BY je.value ORDER BY count DESC, name'
    ).fetchall()
    return jsonify([{'name': r['name'], 'count': r['count']} for r in rows])
