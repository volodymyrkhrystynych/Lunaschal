import struct
import time

from backend.db.connection import get_db, row_to_dict
from backend.ai.embeddings import generate_embedding, generate_embeddings, is_embeddings_configured


def _insert_embedding(id: str, embedding: list[float], source_type: str, source_id: str, chunk_index: int, chunk_text: str) -> None:
    db = get_db()
    blob = struct.pack(f'{len(embedding)}f', *embedding)
    db.execute(
        'INSERT OR REPLACE INTO vec_embeddings(id, embedding) VALUES (?, ?)',
        (id, blob),
    )
    db.execute(
        'INSERT OR REPLACE INTO embedding_metadata(id, source_type, source_id, chunk_index, chunk_text, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (id, source_type, source_id, chunk_index, chunk_text, int(time.time())),
    )
    db.commit()


def _delete_embeddings_by_source(source_type: str, source_id: str) -> None:
    db = get_db()
    ids = [r['id'] for r in db.execute(
        'SELECT id FROM embedding_metadata WHERE source_type=? AND source_id=?',
        (source_type, source_id),
    ).fetchall()]
    for id in ids:
        db.execute('DELETE FROM vec_embeddings WHERE id=?', (id,))
        db.execute('DELETE FROM embedding_metadata WHERE id=?', (id,))
    db.commit()


def _search_similar(query_embedding: list[float], limit: int = 5, source_type: str | None = None) -> list[dict]:
    db = get_db()
    blob = struct.pack(f'{len(query_embedding)}f', *query_embedding)
    sql = """
        SELECT v.id, v.distance, m.source_type, m.source_id, m.chunk_index, m.chunk_text
        FROM vec_embeddings v
        JOIN embedding_metadata m ON v.id = m.id
        WHERE v.embedding MATCH ?
    """
    params: list = [blob]
    if source_type:
        sql += ' AND m.source_type = ?'
        params.append(source_type)
    sql += ' ORDER BY v.distance LIMIT ?'
    params.append(limit)
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def sync_journal_embeddings(journal_id: str) -> int:
    if not is_embeddings_configured():
        return 0
    db = get_db()
    row = db.execute('SELECT * FROM journal_entries WHERE id=?', (journal_id,)).fetchone()
    if not row:
        raise ValueError('Journal entry not found')
    _delete_embeddings_by_source('journal', journal_id)
    content = f"{row['title']}\n\n{row['content']}" if row['title'] else row['content']
    results = generate_embeddings(content)
    from ulid import ULID
    for r in results:
        _insert_embedding(str(ULID()), r['embedding'], 'journal', journal_id, r['chunk_index'], r['chunk_text'])
    return len(results)


def sync_all_journal_embeddings() -> dict:
    if not is_embeddings_configured():
        raise ValueError('Embeddings not configured')
    db = get_db()
    journals = db.execute('SELECT id FROM journal_entries').fetchall()
    total_chunks = 0
    for j in journals:
        try:
            total_chunks += sync_journal_embeddings(j['id'])
        except Exception as e:
            print(f'Failed to sync journal {j["id"]}: {e}')
    return {'synced': len(journals), 'chunks': total_chunks}


def delete_journal_embeddings(journal_id: str) -> None:
    _delete_embeddings_by_source('journal', journal_id)


def search_for_context(query: str, limit: int = 5) -> list[dict]:
    if not is_embeddings_configured():
        return []
    try:
        query_embedding = generate_embedding(query)
        results = _search_similar(query_embedding, limit * 2)
    except Exception:
        return []
    db = get_db()
    seen: dict[str, dict] = {}
    for r in results:
        key = f"{r['source_type']}:{r['source_id']}"
        if key in seen:
            continue
        if r['source_type'] == 'journal':
            row = db.execute('SELECT * FROM journal_entries WHERE id=?', (r['source_id'],)).fetchone()
            if row:
                seen[key] = {
                    'sourceType': r['source_type'],
                    'sourceId': r['source_id'],
                    'content': row['content'],
                    'score': 1 - r['distance'],
                    'metadata': {
                        'title': row['title'],
                        'createdAt': row['created_at'],
                    },
                }
        if len(seen) >= limit:
            break
    return list(seen.values())


def format_rag_context(results: list[dict]) -> str:
    if not results:
        return ''
    sections = []
    for i, r in enumerate(results):
        title = r.get('metadata', {}).get('title') or 'Entry'
        sections.append(f'--- Context {i + 1} [{title}] ---\n{r["content"]}')
    return 'Here is relevant information from the user\'s personal knowledge base:\n\n' + '\n\n'.join(sections)


def get_embedding_stats() -> dict:
    db = get_db()
    total = db.execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0]
    configured = is_embeddings_configured()
    return {'totalJournals': total, 'indexedJournals': 0, 'totalChunks': 0, 'isConfigured': configured}
