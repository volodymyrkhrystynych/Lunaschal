import time
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend.db.connection import get_db, row_to_dict, search_journal_fts
from backend.auth import require_auth
from backend.ai.embeddings import is_embeddings_configured
from backend.ai.rag import sync_journal_embeddings, delete_journal_embeddings, search_for_context

bp = Blueprint('journal', __name__, url_prefix='/api/journal')


@bp.get('')
@require_auth
def list_entries():
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    rows = db.execute(
        'SELECT * FROM journal_entries ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (limit, offset),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/search')
@require_auth
def search():
    query = request.args.get('query', '').strip()
    limit = min(int(request.args.get('limit', 50)), 100)
    if not query:
        return jsonify([])
    fts = search_journal_fts(query, limit)
    if not fts:
        return jsonify([])
    db = get_db()
    id_rank = {r['id']: r['rank'] for r in fts}
    placeholders = ','.join('?' * len(id_rank))
    rows = db.execute(
        f'SELECT * FROM journal_entries WHERE id IN ({placeholders})',
        list(id_rank),
    ).fetchall()
    dicts = sorted([row_to_dict(r) for r in rows], key=lambda d: id_rank.get(d['id'], 0))
    return jsonify(dicts)


@bp.get('/semantic-search')
@require_auth
def semantic_search():
    query = request.args.get('query', '').strip()
    limit = min(int(request.args.get('limit', 5)), 20)
    if not query:
        return jsonify([])
    if not is_embeddings_configured():
        return jsonify([])
    return jsonify(search_for_context(query, limit))


@bp.get('/<id>')
@require_auth
def get_entry(id):
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.post('')
@require_auth
def create_entry():
    body = request.json or {}
    content = body.get('content', '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    now = int(time.time())
    id = str(ULID())
    tags = body.get('tags')
    import json
    get_db().execute(
        'INSERT INTO journal_entries(id, content, title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?)',
        (id, content, body.get('title'), json.dumps(tags) if tags is not None else None, now, now),
    )
    get_db().commit()
    _sync_embeddings_bg(id)
    return jsonify({'id': id}), 201


@bp.patch('/<id>')
@require_auth
def update_entry(id):
    body = request.json or {}
    import json
    updates: dict = {'updated_at': int(time.time())}
    if 'content' in body:
        updates['content'] = body['content']
    if 'title' in body:
        updates['title'] = body['title']
    if 'tags' in body:
        updates['tags'] = json.dumps(body['tags'])
    set_clause = ', '.join(f'{k}=?' for k in updates)
    get_db().execute(
        f'UPDATE journal_entries SET {set_clause} WHERE id=?',
        [*updates.values(), id],
    )
    get_db().commit()
    if 'content' in body or 'title' in body:
        _sync_embeddings_bg(id)
    return jsonify({'success': True})


@bp.delete('/<id>')
@require_auth
def delete_entry(id):
    delete_journal_embeddings(id)
    get_db().execute('DELETE FROM journal_entries WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


def _sync_embeddings_bg(journal_id: str) -> None:
    import threading
    def _sync():
        try:
            if is_embeddings_configured():
                sync_journal_embeddings(journal_id)
        except Exception as e:
            print(f'Embedding sync failed for {journal_id}: {e}')
    threading.Thread(target=_sync, daemon=True).start()
