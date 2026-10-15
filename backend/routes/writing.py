import time
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend.ai.provider import is_ai_configured
from backend.ai.writing import summarize_discussion
from backend.db.connection import build_update, get_db, row_to_dict
from backend.writing import canon_import, canon_tools
from backend.writing import notes as writing_notes

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
    build_update(get_db(), 'writing_projects', updates, 'id=?', (project_id,))
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
    db = get_db()
    build_update(db, 'writing_chapters', updates, 'id=?', (chapter_id,))
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
        'SELECT id, project_id, title, doc_type, pinned, created_at, updated_at FROM writing_context_docs WHERE project_id=? ORDER BY created_at ASC',
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
    now = int(time.time())
    updates: dict = {}
    if 'title' in body:
        updates['title'] = body['title'].strip()
    if 'content' in body:
        updates['content'] = body['content']
    if 'docType' in body:
        updates['doc_type'] = writing_notes.normalize_doc_type(body['docType'])
    if 'pinned' in body:
        updates['pinned'] = 1 if body['pinned'] else 0
    if not updates:
        return jsonify({'success': True})
    db = get_db()
    if 'title' in updates or 'content' in updates:
        # Only text changes move updated_at and leave a revision: a pin is not
        # an edit, and moving updated_at for one would make every pending
        # update card for this note go stale for no reason.
        writing_notes.snapshot(db, doc_id, source='edit', now=now)
        updates['updated_at'] = now
    build_update(db, 'writing_context_docs', updates, 'id=?', (doc_id,))
    db.commit()
    return jsonify({'success': True})


@bp.get('/notes/<doc_id>/revisions')
def list_note_revisions(doc_id):
    rows = get_db().execute(
        'SELECT id, doc_id, title, content, source, message_id, created_at'
        ' FROM writing_context_doc_revisions WHERE doc_id=?'
        ' ORDER BY created_at DESC, id DESC',
        (doc_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/notes/<doc_id>/revisions/<rev_id>/restore')
def restore_note_revision(doc_id, rev_id):
    db = get_db()
    if not writing_notes.restore(db, doc_id, rev_id):
        return jsonify({'error': 'Not found'}), 404
    db.commit()
    row = db.execute('SELECT * FROM writing_context_docs WHERE id=?', (doc_id,)).fetchone()
    return jsonify(row_to_dict(row))


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


# --- Canon sources (Fandom wiki dumps, see backend/writing/canon_import.py) ---

def _canon_dict(row) -> dict:
    out = row_to_dict(row)
    out.pop('filename', None)
    live = canon_import.progress(row['id'])
    if live:
        out['progress'] = live
    return out


@bp.get('/canon')
def list_canon():
    rows = get_db().execute(
        'SELECT * FROM writing_canon_sources ORDER BY created_at DESC'
    ).fetchall()
    return jsonify([_canon_dict(r) for r in rows])


def _new_canon_row(name: str, source_url: str | None = None) -> str:
    now = int(time.time())
    source_id = str(ULID())
    get_db().execute(
        'INSERT INTO writing_canon_sources(id, name, source_url, status, created_at, updated_at)'
        " VALUES (?,?,?,'importing',?,?)",
        (source_id, name or 'Canon wiki', source_url, now, now),
    )
    get_db().commit()
    return source_id


@bp.post('/canon')
def upload_canon():
    """A dump file the user already has. Streamed to disk under CANON_ROOT and
    parsed on a background thread; the row reports progress until then."""
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return jsonify({'error': 'file required'}), 400
    name = (request.form.get('name') or '').strip()
    source_id = _new_canon_row(name)
    root = canon_tools.canon_root()
    root.mkdir(parents=True, exist_ok=True)
    staged = root / f'.{source_id}.upload'
    written = 0
    with staged.open('wb') as out:
        while chunk := upload.stream.read(1 << 20):
            written += len(chunk)
            if written > canon_import.MAX_DOWNLOAD_BYTES:
                out.close()
                staged.unlink(missing_ok=True)
                get_db().execute('DELETE FROM writing_canon_sources WHERE id=?', (source_id,))
                get_db().commit()
                return jsonify({'error': 'That file is too large to be a pages dump'}), 413
            out.write(chunk)
    canon_import.start(source_id, path=staged, name=name)
    return jsonify({'id': source_id}), 202


@bp.post('/canon/fetch')
def fetch_canon():
    """A wiki URL (its dump is found on Special:Statistics) or a dump URL.
    Every redirect hop goes through backend/research/web.assert_public_url."""
    body = request.json or {}
    url = (body.get('url') or '').strip()
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'url must be an http(s) link'}), 400
    name = (body.get('name') or '').strip()
    source_id = _new_canon_row(name, url)
    canon_import.start(source_id, url=url, name=name)
    return jsonify({'id': source_id}), 202


@bp.patch('/canon/<source_id>')
def rename_canon(source_id):
    name = ((request.json or {}).get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    get_db().execute('UPDATE writing_canon_sources SET name=?, updated_at=? WHERE id=?',
                     (name, int(time.time()), source_id))
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/canon/<source_id>')
def delete_canon(source_id):
    db = get_db()
    row = db.execute('SELECT status, filename FROM writing_canon_sources WHERE id=?',
                     (source_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    if row['status'] == 'importing':
        return jsonify({'error': 'Wait for the import to finish'}), 409
    if row['filename']:
        canon_tools.path_for(dict(row)).unlink(missing_ok=True)
    db.execute('DELETE FROM writing_canon_sources WHERE id=?', (source_id,))
    db.commit()
    return jsonify({'success': True})


@bp.get('/projects/<project_id>/canon')
def project_canon(project_id):
    rows = get_db().execute(
        'SELECT source_id FROM writing_project_canon WHERE project_id=?', (project_id,)
    ).fetchall()
    return jsonify({'sourceIds': [r['source_id'] for r in rows]})


@bp.put('/projects/<project_id>/canon')
def set_project_canon(project_id):
    ids = (request.json or {}).get('sourceIds')
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return jsonify({'error': 'sourceIds must be a list of ids'}), 400
    db = get_db()
    if not db.execute('SELECT 1 FROM writing_projects WHERE id=?', (project_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    known = {r['id'] for r in db.execute('SELECT id FROM writing_canon_sources').fetchall()}
    unknown = [i for i in ids if i not in known]
    if unknown:
        return jsonify({'error': f'unknown canon source {unknown[0]}'}), 400
    now = int(time.time())
    db.execute('DELETE FROM writing_project_canon WHERE project_id=?', (project_id,))
    db.executemany(
        'INSERT INTO writing_project_canon(project_id, source_id, created_at) VALUES (?,?,?)',
        [(project_id, i, now) for i in dict.fromkeys(ids)],
    )
    db.commit()
    return jsonify({'sourceIds': list(dict.fromkeys(ids))})
