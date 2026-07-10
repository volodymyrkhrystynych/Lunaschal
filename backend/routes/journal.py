import json
import queue
import threading
import time
from flask import Blueprint, Response, jsonify, request, stream_with_context
from ulid import ULID
from backend.db.connection import get_db, row_to_dict, search_journal_fts
from backend.ai.embeddings import is_embeddings_configured
from backend.ai.rag import sync_journal_embeddings, delete_journal_embeddings, search_for_context
from backend.ai.journal import polish_journal_entry, generate_journal_metadata

bp = Blueprint('journal', __name__, url_prefix='/api/journal')

_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def _notify_subscribers(entry_id: str) -> None:
    with _subscribers_lock:
        for q in _subscribers:
            q.put(entry_id)


@bp.get('/events')
def events():
    q: queue.Queue = queue.Queue()
    with _subscribers_lock:
        _subscribers.append(q)

    def generate():
        try:
            while True:
                try:
                    entry_id = q.get(timeout=30)
                    yield f'data: {json.dumps({"id": entry_id})}\n\n'
                except queue.Empty:
                    yield ': heartbeat\n\n'
        finally:
            with _subscribers_lock:
                _subscribers.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _enrich_with_curated_tags(db, dicts: list[dict]) -> list[dict]:
    if not dicts:
        return dicts
    ids = [d['id'] for d in dicts]
    placeholders = ','.join('?' * len(ids))
    tag_rows = db.execute(
        f'SELECT jec.entry_id, ct.name'
        f' FROM journal_entry_curated_tags jec'
        f' JOIN curated_tags ct ON ct.id = jec.tag_id'
        f' WHERE jec.entry_id IN ({placeholders})',
        ids,
    ).fetchall()
    tag_map: dict[str, list[str]] = {}
    for tr in tag_rows:
        tag_map.setdefault(tr['entry_id'], []).append(tr['name'])
    for d in dicts:
        d['curatedTags'] = tag_map.get(d['id'], [])
    return dicts


def _enrich_with_fic_refs(db, dicts: list[dict]) -> list[dict]:
    if not dicts:
        return dicts
    ids = [d['id'] for d in dicts]
    placeholders = ','.join('?' * len(ids))
    ref_rows = db.execute(
        f'SELECT jefr.journal_entry_id, jefr.fic_id, f.title AS fic_title,'
        f' jefr.chapter_id, fc.title AS chapter_title'
        f' FROM journal_entry_fic_refs jefr'
        f' JOIN fics f ON f.id = jefr.fic_id'
        f' LEFT JOIN fic_chapters fc ON fc.id = jefr.chapter_id'
        f' WHERE jefr.journal_entry_id IN ({placeholders})',
        ids,
    ).fetchall()
    ref_map: dict[str, list[dict]] = {}
    for r in ref_rows:
        ref_map.setdefault(r['journal_entry_id'], []).append({
            'ficId': r['fic_id'],
            'ficTitle': r['fic_title'],
            'chapterId': r['chapter_id'],
            'chapterTitle': r['chapter_title'],
        })
    for d in dicts:
        d['ficRefs'] = ref_map.get(d['id'], [])
    return dicts


@bp.get('')
def list_entries():
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    curated_tag_id = request.args.get('curated_tag_id')
    db = get_db()
    if curated_tag_id:
        rows = db.execute(
            'SELECT je.* FROM journal_entries je'
            ' JOIN journal_entry_curated_tags jec ON je.id = jec.entry_id'
            ' WHERE jec.tag_id = ?'
            ' ORDER BY je.created_at DESC LIMIT ? OFFSET ?',
            (curated_tag_id, limit, offset),
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT * FROM journal_entries ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (limit, offset),
        ).fetchall()
    dicts = [row_to_dict(r) for r in rows]
    return jsonify(_enrich_with_fic_refs(db, _enrich_with_curated_tags(db, dicts)))


@bp.get('/search')
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
    return jsonify(_enrich_with_fic_refs(db, _enrich_with_curated_tags(db, dicts)))


@bp.get('/semantic-search')
def semantic_search():
    query = request.args.get('query', '').strip()
    limit = min(int(request.args.get('limit', 5)), 20)
    if not query:
        return jsonify([])
    if not is_embeddings_configured():
        return jsonify([])
    return jsonify(search_for_context(query, limit))


@bp.get('/<id>')
def get_entry(id):
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.post('')
def create_entry():
    body = request.json or {}
    raw_content = body.get('raw_content', '').strip()
    content = body.get('content', '').strip()

    if raw_content:
        # STT path: save immediately with raw text, polish in background
        content = raw_content
    elif not content:
        return jsonify({'error': 'content required'}), 400
    else:
        raw_content = None

    title = body.get('title') or None
    tags = body.get('tags') or None

    now = int(time.time())
    id = str(ULID())
    get_db().execute(
        'INSERT INTO journal_entries(id, content, raw_content, title, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (id, content, raw_content, title, json.dumps(tags) if tags is not None else None, now, now),
    )
    get_db().commit()
    _notify_subscribers(id)
    _sync_embeddings_bg(id)
    if raw_content:
        _polish_bg(id, raw_content)
    if not title or not tags:
        _generate_metadata_bg(id, content)
    return jsonify({'id': id}), 201


@bp.post('/<id>/polish')
def polish_entry(id):
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    source = row['raw_content'] or ''
    if not source.strip():
        return jsonify({'error': 'No original transcription to polish'}), 400
    polished = polish_journal_entry(source)
    db = get_db()
    db.execute(
        'UPDATE journal_entries SET content=?, updated_at=? WHERE id=?',
        (polished, int(time.time()), id),
    )
    db.commit()
    _notify_subscribers(id)
    # Regenerate title/tags from the polished text if they're missing
    entry = row_to_dict(db.execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone())
    if not entry.get('title') or not entry.get('tags'):
        _generate_metadata_bg(id, polished)
    return jsonify({'success': True, 'content': polished})


@bp.patch('/<id>')
def update_entry(id):
    body = request.json or {}
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
def delete_entry(id):
    delete_journal_embeddings(id)
    get_db().execute('DELETE FROM journal_entries WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


def _polish_bg(journal_id: str, raw_content: str) -> None:
    def _run():
        try:
            polished = polish_journal_entry(raw_content)
            if polished == raw_content:
                return
            db = get_db()
            db.execute(
                'UPDATE journal_entries SET content=?, updated_at=? WHERE id=?',
                (polished, int(time.time()), journal_id),
            )
            db.commit()
            _notify_subscribers(journal_id)
        except Exception as e:
            print(f'Background polish failed for {journal_id}: {e}')
    threading.Thread(target=_run, daemon=True).start()


def _generate_metadata_bg(journal_id: str, content: str) -> None:
    def _run():
        try:
            meta = generate_journal_metadata(content)
            if not meta:
                return
            updates: dict = {}
            if meta.get('title'):
                updates['title'] = meta['title']
            if meta.get('tags'):
                updates['tags'] = json.dumps(meta['tags'])
            if not updates:
                return
            db = get_db()
            set_clause = ', '.join(f'{k}=?' for k in updates)
            db.execute(
                f'UPDATE journal_entries SET {set_clause} WHERE id=?',
                [*updates.values(), journal_id],
            )
            db.commit()
            _notify_subscribers(journal_id)
        except Exception as e:
            print(f'Background metadata generation failed for {journal_id}: {e}')
    threading.Thread(target=_run, daemon=True).start()


def _sync_embeddings_bg(journal_id: str) -> None:
    def _sync():
        try:
            if is_embeddings_configured():
                sync_journal_embeddings(journal_id)
        except Exception as e:
            print(f'Embedding sync failed for {journal_id}: {e}')
    threading.Thread(target=_sync, daemon=True).start()
