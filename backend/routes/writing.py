import re
import time
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend.ai.provider import is_ai_configured
from backend.ai.writing import summarize_discussion
from backend.day_boundary import day_key_for
from backend.db.connection import build_update, get_db, row_to_dict
from backend.writing import canon_import, canon_tools, revisioned
from backend.writing import chapters as writing_chapters
from backend.writing import notes as writing_notes

bp = Blueprint('writing', __name__, url_prefix='/api/writing')

_CLIENT_ID = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')
MAX_STATS_DAYS = 366


def _client_or_new_id(body: dict) -> str | None:
    """The ULID a client minted, so a create can happen offline and be replayed,
    or a fresh one. None when the client sent something else."""
    value = body.get('id')
    if value is None:
        return str(ULID())
    return value if isinstance(value, str) and _CLIENT_ID.match(value) else None


def _captured_at(body: dict, now: int) -> int:
    """When the words were written: a client's claim, never after now."""
    value = body.get('capturedAt')
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return now
    return min(value, now)


def _touch_project(db, project_id: str | None, now: int) -> None:
    """A project's `updated_at` is how a client learns the book changed."""
    if project_id:
        db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, project_id))


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
    if 'dailyWordGoal' in body:
        goal = body['dailyWordGoal']
        if goal is not None and (isinstance(goal, bool) or not isinstance(goal, int) or goal <= 0):
            return jsonify({'error': 'dailyWordGoal must be a positive whole number or null'}), 400
        updates['daily_word_goal'] = goal
    build_update(get_db(), 'writing_projects', updates, 'id=?', (project_id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.get('/projects/<project_id>/bundle')
def project_bundle(project_id):
    """A whole book, bodies included: what the phone keeps on disk to read and
    edit it offline. Every revision is read in the one call, so the copy is
    self-consistent."""
    db = get_db()
    project = db.execute('SELECT * FROM writing_projects WHERE id=?', (project_id,)).fetchone()
    if not project:
        return jsonify({'error': 'Not found'}), 404
    chapters = db.execute(
        'SELECT * FROM writing_chapters WHERE project_id=? ORDER BY position ASC', (project_id,)
    ).fetchall()
    notes = db.execute(
        'SELECT * FROM writing_context_docs WHERE project_id=? ORDER BY created_at ASC', (project_id,)
    ).fetchall()
    return jsonify({
        'project': row_to_dict(project),
        'chapters': [row_to_dict(r) for r in chapters],
        'notes': [row_to_dict(r) for r in notes],
    })


@bp.get('/projects/<project_id>/stats')
def project_stats(project_id):
    """Net words per 4am day, newest first, and the day's goal. Days with no
    writing are simply absent, and a day that cut more than it added is
    negative: rounding that up to zero is the reader's choice."""
    db = get_db()
    project = db.execute('SELECT daily_word_goal FROM writing_projects WHERE id=?', (project_id,)).fetchone()
    if not project:
        return jsonify({'error': 'Not found'}), 404
    days = max(1, min(request.args.get('days', default=7, type=int), MAX_STATS_DAYS))
    rows = db.execute(
        'SELECT day, words FROM writing_daily_words WHERE project_id=? ORDER BY day DESC LIMIT ?',
        (project_id, days),
    ).fetchall()
    today = day_key_for()
    return jsonify({
        'today': today,
        'todayWords': next((r['words'] for r in rows if r['day'] == today), 0),
        'dailyWordGoal': project['daily_word_goal'],
        'days': [{'day': r['day'], 'words': r['words']} for r in rows],
    })


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
        'SELECT id, project_id, title, position, revision, created_at, updated_at FROM writing_chapters WHERE project_id=? ORDER BY position ASC',
        (project_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/projects/<project_id>/chapters')
def create_chapter(project_id):
    body = request.json or {}
    title = body.get('title', '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    id = _client_or_new_id(body)
    if id is None:
        return jsonify({'error': 'id must be a ULID'}), 400
    db = get_db()
    if not db.execute('SELECT 1 FROM writing_projects WHERE id=?', (project_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    row = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM writing_chapters WHERE project_id=?',
        (project_id,),
    ).fetchone()
    position = body.get('position', row['next_pos'])
    now = int(time.time())
    # A replay of a create the phone queued offline lands here and changes nothing.
    db.execute(
        'INSERT OR IGNORE INTO writing_chapters(id, project_id, title, content, position, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, project_id, title, '', position, now, now),
    )
    db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, project_id))
    db.commit()
    return jsonify({'id': id}), 201


@bp.put('/projects/<project_id>/chapters/order')
def order_chapters(project_id):
    """Set the whole order at once. It must name exactly the project's chapters:
    a list from before a chapter was added elsewhere would otherwise drop it to
    an arbitrary place."""
    ids = (request.json or {}).get('ids')
    if not isinstance(ids, list):
        return jsonify({'error': 'ids required'}), 400
    db = get_db()
    existing = {r['id'] for r in db.execute(
        'SELECT id FROM writing_chapters WHERE project_id=?', (project_id,))}
    if len(ids) != len(existing) or set(ids) != existing:
        return jsonify({'error': 'the chapter list has changed; reload and try again'}), 409
    now = int(time.time())
    db.executemany('UPDATE writing_chapters SET position=? WHERE id=?',
                   [(position, id) for position, id in enumerate(ids)])
    _touch_project(db, project_id, now)
    db.commit()
    return jsonify({'success': True})


@bp.get('/chapters/<chapter_id>')
def get_chapter(chapter_id):
    row = get_db().execute('SELECT * FROM writing_chapters WHERE id=?', (chapter_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


def _conflict_response(conflict: revisioned.DocumentConflict):
    return jsonify({'error': 'changed on another device', 'conflict': True,
                    'current': row_to_dict(conflict.current)}), 409


@bp.patch('/chapters/<chapter_id>')
def update_chapter(chapter_id):
    body = request.json or {}
    now = int(time.time())
    try:
        base = revisioned.base_revision_of(body)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    text: dict = {}
    if 'title' in body:
        text['title'] = body['title'].strip()
    if 'content' in body:
        text['content'] = body['content']
    extra = {'position': int(body['position'])} if 'position' in body else None
    db = get_db()
    with revisioned.LOCK:
        try:
            row, revision = revisioned.save(
                db, 'writing_chapters', chapter_id, text, extra=extra, base_revision=base, now=now,
                before_write=lambda db, id, at: writing_chapters.snapshot(db, id, source='edit', now=at))
        except revisioned.DocumentMissing:
            return jsonify({'error': 'Not found'}), 404
        except revisioned.DocumentConflict as e:
            return _conflict_response(e)
        if 'content' in text:
            writing_chapters.record_words(db, row['project_id'], row['content'], text['content'],
                                          _captured_at(body, now))
        _touch_project(db, row['project_id'], now)
        db.commit()
    return jsonify({'success': True, 'revision': revision})


@bp.get('/chapters/<chapter_id>/revisions')
def list_chapter_revisions(chapter_id):
    rows = get_db().execute(
        'SELECT id, chapter_id, title, content, source, message_id, created_at'
        ' FROM writing_chapter_revisions WHERE chapter_id=?'
        ' ORDER BY created_at DESC, id DESC',
        (chapter_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/chapters/<chapter_id>/revisions/<rev_id>/restore')
def restore_chapter_revision(chapter_id, rev_id):
    db = get_db()
    if not writing_chapters.restore(db, chapter_id, rev_id):
        return jsonify({'error': 'Not found'}), 404
    db.commit()
    row = db.execute('SELECT * FROM writing_chapters WHERE id=?', (chapter_id,)).fetchone()
    return jsonify(row_to_dict(row))


@bp.delete('/chapters/<chapter_id>')
def delete_chapter(chapter_id):
    db = get_db()
    row = db.execute('SELECT project_id FROM writing_chapters WHERE id=?', (chapter_id,)).fetchone()
    db.execute('DELETE FROM writing_chapters WHERE id=?', (chapter_id,))
    _touch_project(db, row['project_id'] if row else None, int(time.time()))
    db.commit()
    return jsonify({'success': True})


# --- Notes (stored in the writing_context_docs table) ---

@bp.get('/projects/<project_id>/notes')
def list_notes(project_id):
    rows = get_db().execute(
        'SELECT id, project_id, title, doc_type, pinned, revision, created_at, updated_at FROM writing_context_docs WHERE project_id=? ORDER BY created_at ASC',
        (project_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/projects/<project_id>/notes')
def create_note(project_id):
    body = request.json or {}
    title = body.get('title', '').strip()
    if not title:
        return jsonify({'error': 'title required'}), 400
    id = _client_or_new_id(body)
    if id is None:
        return jsonify({'error': 'id must be a ULID'}), 400
    now = int(time.time())
    db = get_db()
    if not db.execute('SELECT 1 FROM writing_projects WHERE id=?', (project_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    # A replay of a create the phone queued offline lands here and changes nothing.
    db.execute(
        'INSERT OR IGNORE INTO writing_context_docs(id, project_id, title, content, doc_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, project_id, title, body.get('content', ''), writing_notes.normalize_doc_type(body.get('docType')), now, now),
    )
    _touch_project(db, project_id, now)
    db.commit()
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
    try:
        base = revisioned.base_revision_of(body)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    text: dict = {}
    if 'title' in body:
        text['title'] = body['title'].strip()
    if 'content' in body:
        text['content'] = body['content']
    # Only text changes move updated_at and leave a revision: a pin is not an
    # edit, and moving updated_at for one would make every pending update card
    # for this note go stale for no reason.
    extra: dict = {}
    if 'docType' in body:
        extra['doc_type'] = writing_notes.normalize_doc_type(body['docType'])
    if 'pinned' in body:
        extra['pinned'] = 1 if body['pinned'] else 0
    db = get_db()
    with revisioned.LOCK:
        try:
            row, revision = revisioned.save(
                db, 'writing_context_docs', doc_id, text, extra=extra, base_revision=base, now=now,
                before_write=lambda db, id, at: writing_notes.snapshot(db, id, source='edit', now=at))
        except revisioned.DocumentMissing:
            return jsonify({'error': 'Not found'}), 404
        except revisioned.DocumentConflict as e:
            return _conflict_response(e)
        _touch_project(db, row['project_id'], now)
        db.commit()
    return jsonify({'success': True, 'revision': revision})


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
    db = get_db()
    row = db.execute('SELECT project_id FROM writing_context_docs WHERE id=?', (doc_id,)).fetchone()
    db.execute('DELETE FROM writing_context_docs WHERE id=?', (doc_id,))
    _touch_project(db, row['project_id'] if row else None, int(time.time()))
    db.commit()
    return jsonify({'success': True})


# --- Discussions (writing-scoped conversations) ---

@bp.get('/projects/<project_id>/conversations')
def list_discussions(project_id):
    rows = get_db().execute(
        'SELECT * FROM conversations WHERE writing_project_id=? ORDER BY updated_at DESC',
        (project_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


# A selection is a passage to work on, not a chapter to paste: past this the
# prompt would be carrying a chapter, which writing_read already reads.
MAX_PASSAGE_CHARS = 6000


def _passage_title(passage: str | None) -> str | None:
    if not passage:
        return None
    words = ' '.join(passage.split())
    return 'Rewrite: ' + (words if len(words) <= 40 else words[:40].rstrip() + '…')


@bp.post('/projects/<project_id>/conversations')
def create_discussion(project_id):
    body = request.json or {}
    now = int(time.time())
    id = str(ULID())
    db = get_db()
    chapter_id = body.get('chapterId')
    if chapter_id is not None and not db.execute(
            'SELECT 1 FROM writing_chapters WHERE id=? AND project_id=?',
            (chapter_id, project_id)).fetchone():
        return jsonify({'error': 'that chapter is not in this project'}), 400
    passage = body.get('passage')
    if passage is not None:
        if not isinstance(passage, str) or not passage.strip() or chapter_id is None:
            return jsonify({'error': 'a passage needs its text and the chapter it is from'}), 400
        if len(passage) > MAX_PASSAGE_CHARS:
            return jsonify({'error': f'select under {MAX_PASSAGE_CHARS:,} characters'}), 400
    db.execute(
        'INSERT INTO conversations(id, title, writing_project_id, writing_chapter_id, writing_passage,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, body.get('title') or _passage_title(passage) or 'New Discussion', project_id, chapter_id,
         passage, now, now),
    )
    db.commit()
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


# The phone's canon browser: the same project-scoped search and read the
# discussion's canon_search / canon_read run, so a person sees exactly what the
# model can see — and nothing from a wiki this book is not linked to.

@bp.get('/projects/<project_id>/canon/search')
def search_project_canon(project_id):
    try:
        limit = max(1, min(int(request.args.get('limit', 20)), 50))
    except ValueError:
        limit = 20
    return jsonify(canon_tools.CanonTools(project_id).hits(request.args.get('q', ''), limit))


@bp.get('/projects/<project_id>/canon/read')
def read_project_canon(project_id):
    try:
        return jsonify(canon_tools.CanonTools(project_id).article(request.args.get('id', '')))
    except LookupError:
        return jsonify({'error': 'Not found'}), 404
