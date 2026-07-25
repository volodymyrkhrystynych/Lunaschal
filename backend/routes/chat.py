import time
import json
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ulid import ULID
from backend.db.connection import get_db, row_to_dict
from backend.chat_day import day_key_for
from backend.ai.provider import is_ai_configured
from backend.ai.chat import chat_stream, build_chat_system_prompt
from backend.ai.chat_title import generate_conversation_title
from backend.ai.classifier import classify_intent, should_classify
from backend.ai.rag import search_for_context, format_rag_context
from backend.ai.embeddings import is_embeddings_configured

bp = Blueprint('chat', __name__, url_prefix='/api/chat')


@bp.get('/conversations')
def list_conversations():
    rows = get_db().execute(
        'SELECT * FROM conversations WHERE writing_project_id IS NULL ORDER BY updated_at DESC'
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/today')
def get_today():
    """The current chat day's conversation with its messages, or null if none yet."""
    db = get_db()
    row = db.execute(
        'SELECT * FROM conversations WHERE day_key=? AND writing_project_id IS NULL',
        (day_key_for(),),
    ).fetchone()
    if not row:
        return jsonify(None)
    conv = row_to_dict(row)
    msgs = db.execute(
        'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (conv['id'],)
    ).fetchall()
    conv['messages'] = [row_to_dict(m) for m in msgs]
    return jsonify(conv)


@bp.get('/journal-conversations')
def journal_conversations():
    """Past chat days for the Journal feed (excludes the current live day)."""
    rows = get_db().execute(
        '''SELECT c.id, c.title, c.day_key, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM messages m
                   WHERE m.conversation_id = c.id AND m.role IN ('user', 'assistant')) AS message_count
           FROM conversations c
           WHERE c.day_key IS NOT NULL AND c.day_key < ? AND c.writing_project_id IS NULL
             AND EXISTS (SELECT 1 FROM messages m2
                         WHERE m2.conversation_id = c.id AND m2.role IN ('user', 'assistant'))
           ORDER BY c.day_key DESC''',
        (day_key_for(),),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/conversations/<id>')
def get_conversation(id):
    db = get_db()
    row = db.execute('SELECT * FROM conversations WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify(None)
    conv = row_to_dict(row)
    msgs = db.execute(
        'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (id,)
    ).fetchall()
    conv['messages'] = [row_to_dict(m) for m in msgs]
    return jsonify(conv)


@bp.post('/conversations')
def create_conversation():
    """Find-or-create the current chat day's conversation. Titles stay NULL until
    the nightly title job fills them in."""
    db = get_db()
    dk = day_key_for()
    existing = db.execute(
        'SELECT id FROM conversations WHERE day_key=? AND writing_project_id IS NULL',
        (dk,),
    ).fetchone()
    if existing:
        return jsonify({'id': existing['id']}), 200
    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO conversations(id, title, day_key, created_at, updated_at) VALUES (?,?,?,?,?)',
        (id, None, dk, now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/conversations/<id>/generate-title')
def generate_title(id):
    """Synchronously (re)generate an AI title for one conversation."""
    db = get_db()
    msgs = db.execute(
        'SELECT role, content, metadata FROM messages WHERE conversation_id=? ORDER BY created_at',
        (id,),
    ).fetchall()
    title = generate_conversation_title([dict(m) for m in msgs])
    if not title:
        return jsonify({'title': None})
    db.execute('UPDATE conversations SET title=? WHERE id=?', (title, id))
    db.commit()
    return jsonify({'title': title})


@bp.patch('/conversations/<id>/title')
def update_title(id):
    body = request.json or {}
    title = body.get('title', '')
    get_db().execute(
        'UPDATE conversations SET title=?, updated_at=? WHERE id=?',
        (title, int(time.time()), id),
    )
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/conversations/<id>')
def delete_conversation(id):
    get_db().execute('DELETE FROM conversations WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.post('/conversations/<id>/messages')
def add_message(id):
    body = request.json or {}
    msg_id = str(ULID())
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at) VALUES (?,?,?,?,?,?)',
        (msg_id, id, body.get('role'), body.get('content'), body.get('metadata'), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, id))
    db.commit()
    return jsonify({'id': msg_id}), 201


@bp.post('/classify')
def classify():
    body = request.json or {}
    message = body.get('message', '')
    if not should_classify(message):
        return jsonify({'intent': 'conversation', 'confidence': 1.0})
    return jsonify(classify_intent(message))


@bp.post('/save-journal')
def save_journal():
    body = request.json or {}
    now = int(time.time())
    id = str(ULID())
    tags = body.get('tags', [])
    db = get_db()
    db.execute(
        'INSERT INTO journal_entries(id, content, title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?)',
        (id, body.get('content', ''), body.get('title'), json.dumps(tags), now, now),
    )
    if body.get('messageId'):
        _update_message_metadata(db, body['messageId'], 'savedAsJournal', id)
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/save-calendar')
def save_calendar():
    body = request.json or {}
    now = int(time.time())
    id = str(ULID())
    tags = body.get('tags', [])
    db = get_db()
    db.execute(
        'INSERT INTO calendar_events(id, title, description, date, time, tags, created_at) VALUES (?,?,?,?,?,?,?)',
        (id, body.get('title', ''), body.get('description', ''),
         body.get('date', ''), body.get('time'), json.dumps(tags), now),
    )
    if body.get('messageId'):
        _update_message_metadata(db, body['messageId'], 'savedAsCalendar', id)
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/rag-context')
def rag_context():
    body = request.json or {}
    message = body.get('message', '')
    limit = min(int(body.get('limit', 3)), 10)
    if not is_embeddings_configured():
        return jsonify({'context': '', 'results': [], 'isConfigured': False})
    results = search_for_context(message, limit)
    context = format_rag_context(results)
    return jsonify({
        'context': context,
        'results': [
            {
                'sourceId': r['sourceId'],
                'sourceType': r['sourceType'],
                'title': r.get('metadata', {}).get('title'),
                'score': r['score'],
                'preview': r['content'][:200] + ('...' if len(r['content']) > 200 else ''),
            }
            for r in results
        ],
        'isConfigured': True,
    })


@bp.post('/stream')
def stream():
    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400
    body = request.json or {}
    messages = body.get('messages', [])
    rag_context = body.get('ragContext', '')
    system_prompt = body.get('systemPrompt', '')
    if not system_prompt:
        # Plain chat (no caller-supplied prompt, e.g. the Chat tab) gets the
        # default prompt enriched with the last day's journal entries.
        system_prompt = build_chat_system_prompt()

    def generate():
        try:
            for chunk in chat_stream(messages, rag_context, system_prompt):
                yield f'data: {json.dumps({"content": chunk})}\n\n'
            yield 'data: [DONE]\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )


def _update_message_metadata(db, message_id: str, key: str, value: str) -> None:
    row = db.execute('SELECT metadata FROM messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        return
    meta = json.loads(row['metadata']) if row['metadata'] else {}
    meta[key] = value
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), message_id))
