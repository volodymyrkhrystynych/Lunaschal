import time
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend.ai.provider import is_ai_configured
from backend.ai.writing import summarize_discussion
from backend.db.connection import get_db, row_to_dict

bp = Blueprint('writing', __name__, url_prefix='/api/writing')


# --- Projects ---

@bp.get('/projects')
def list_projects():
    rows = get_db().execute(
        'SELECT * FROM writing_projects ORDER BY updated_at DESC'
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/projects')
def create_project():
    body = request.json or {}
    title = body.get('title', '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    now = int(time.time())
    id = str(ULID())
    get_db().execute(
        'INSERT INTO writing_projects(id, title, description, created_at, updated_at) VALUES (?,?,?,?,?)',
        (id, title, body.get('description'), now, now),
    )
    get_db().commit()
    return jsonify({'id': id}), 201


@bp.get('/projects/<project_id>')
def get_project(project_id):
    row = get_db().execute('SELECT * FROM writing_projects WHERE id=?', (project_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.patch('/projects/<project_id>')
def update_project(project_id):
    body = request.json or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'title' in body:
        updates['title'] = body['title'].strip()
    if 'description' in body:
        updates['description'] = body['description']
    set_clause = ', '.join(f'{k}=?' for k in updates)
    get_db().execute(
        f'UPDATE writing_projects SET {set_clause} WHERE id=?',
        [*updates.values(), project_id],
    )
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/projects/<project_id>')
def delete_project(project_id):
    db = get_db()
    db.execute('DELETE FROM conversations WHERE writing_project_id=?', (project_id,))
    db.execute('DELETE FROM writing_projects WHERE id=?', (project_id,))
    db.commit()
    return jsonify({'success': True})


# --- Chapters ---

@bp.get('/projects/<project_id>/chapters')
def list_chapters(project_id):
    rows = get_db().execute(
        'SELECT id, project_id, title, position, created_at, updated_at FROM writing_chapters WHERE project_id=? ORDER BY position ASC',
        (project_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/projects/<project_id>/chapters')
def create_chapter(project_id):
    body = request.json or {}
    title = body.get('title', '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    db = get_db()
    row = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM writing_chapters WHERE project_id=?',
        (project_id,),
    ).fetchone()
    position = body.get('position', row['next_pos'])
    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO writing_chapters(id, project_id, title, content, position, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, project_id, title, '', position, now, now),
    )
    db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, project_id))
    db.commit()
    return jsonify({'id': id}), 201


@bp.get('/chapters/<chapter_id>')
def get_chapter(chapter_id):
    row = get_db().execute('SELECT * FROM writing_chapters WHERE id=?', (chapter_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.patch('/chapters/<chapter_id>')
def update_chapter(chapter_id):
    body = request.json or {}
    now = int(time.time())
    updates: dict = {'updated_at': now}
    if 'title' in body:
        updates['title'] = body['title'].strip()
    if 'content' in body:
        updates['content'] = body['content']
    if 'position' in body:
        updates['position'] = int(body['position'])
    set_clause = ', '.join(f'{k}=?' for k in updates)
    db = get_db()
    db.execute(
        f'UPDATE writing_chapters SET {set_clause} WHERE id=?',
        [*updates.values(), chapter_id],
    )
    row = db.execute('SELECT project_id FROM writing_chapters WHERE id=?', (chapter_id,)).fetchone()
    if row:
        db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, row['project_id']))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/chapters/<chapter_id>')
def delete_chapter(chapter_id):
    get_db().execute('DELETE FROM writing_chapters WHERE id=?', (chapter_id,))
    get_db().commit()
    return jsonify({'success': True})


# --- Notes (stored in the writing_context_docs table) ---

@bp.get('/projects/<project_id>/notes')
def list_notes(project_id):
    rows = get_db().execute(
        'SELECT id, project_id, title, doc_type, created_at, updated_at FROM writing_context_docs WHERE project_id=? ORDER BY created_at ASC',
        (project_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/projects/<project_id>/notes')
def create_note(project_id):
    body = request.json or {}
    title = body.get('title', '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    now = int(time.time())
    id = str(ULID())
    get_db().execute(
        'INSERT INTO writing_context_docs(id, project_id, title, content, doc_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, project_id, title, body.get('content', ''), body.get('docType', 'note'), now, now),
    )
    get_db().commit()
    return jsonify({'id': id}), 201


@bp.get('/notes/<doc_id>')
def get_note(doc_id):
    row = get_db().execute('SELECT * FROM writing_context_docs WHERE id=?', (doc_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.patch('/notes/<doc_id>')
def update_note(doc_id):
    body = request.json or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'title' in body:
        updates['title'] = body['title'].strip()
    if 'content' in body:
        updates['content'] = body['content']
    if 'docType' in body:
        updates['doc_type'] = body['docType']
    set_clause = ', '.join(f'{k}=?' for k in updates)
    get_db().execute(
        f'UPDATE writing_context_docs SET {set_clause} WHERE id=?',
        [*updates.values(), doc_id],
    )
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/notes/<doc_id>')
def delete_note(doc_id):
    get_db().execute('DELETE FROM writing_context_docs WHERE id=?', (doc_id,))
    get_db().commit()
    return jsonify({'success': True})


# --- Discussions (writing-scoped conversations) ---

@bp.get('/projects/<project_id>/conversations')
def list_discussions(project_id):
    rows = get_db().execute(
        'SELECT * FROM conversations WHERE writing_project_id=? ORDER BY updated_at DESC',
        (project_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/projects/<project_id>/conversations')
def create_discussion(project_id):
    body = request.json or {}
    now = int(time.time())
    id = str(ULID())
    get_db().execute(
        'INSERT INTO conversations(id, title, writing_project_id, created_at, updated_at) VALUES (?,?,?,?,?)',
        (id, body.get('title') or 'New Discussion', project_id, now, now),
    )
    get_db().commit()
    return jsonify({'id': id}), 201


@bp.post('/conversations/<conv_id>/summarize')
def summarize_conversation(conv_id):
    db = get_db()
    conv = db.execute(
        'SELECT * FROM conversations WHERE id=? AND writing_project_id IS NOT NULL',
        (conv_id,),
    ).fetchone()
    if not conv:
        return jsonify({'error': 'Not found'}), 404
    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400
    msgs = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id=? "
        "AND role IN ('user', 'assistant') ORDER BY created_at ASC",
        (conv_id,),
    ).fetchall()
    if not msgs:
        return jsonify({'error': 'Nothing to summarize yet'}), 400
    project = db.execute(
        'SELECT title, description FROM writing_projects WHERE id=?',
        (conv['writing_project_id'],),
    ).fetchone()
    transcript = '\n\n'.join(
        f"{'Author' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in msgs
    )
    result = summarize_discussion(transcript, project['title'], project['description'])
    if not result:
        return jsonify({'error': 'Summarization failed'}), 502
    now = int(time.time())
    note_id = str(ULID())
    db.execute(
        'INSERT INTO writing_context_docs(id, project_id, title, content, doc_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (note_id, conv['writing_project_id'], result['title'], result['content'], 'note', now, now),
    )
    db.commit()
    row = db.execute('SELECT * FROM writing_context_docs WHERE id=?', (note_id,)).fetchone()
    return jsonify(row_to_dict(row)), 201
