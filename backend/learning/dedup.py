"""Answer-embedding helpers: blob packing, cosine, nearest-active-card search.

Embeddings are best-effort everywhere: when the embedding provider is
unconfigured or errors, callers get None and the dedup hint / grading gate
silently disable (same degradation contract as RAG).
"""
import math
import struct

from backend.db.connection import get_db

# Deliberately over-eager: a false positive costs a glance in the approval
# queue; a miss costs review time via FSRS over-drilling the same fact.
DEDUP_THRESHOLD = 0.82


def embed_answer(text: str) -> bytes | None:
    from backend.ai.embeddings import generate_embedding, is_embeddings_configured
    if not is_embeddings_configured():
        return None
    try:
        vec = generate_embedding(text)
        return struct.pack(f'{len(vec)}f', *vec)
    except Exception:
        return None


def cosine(a: bytes | None, b: bytes | None) -> float | None:
    """Cosine similarity of two packed float32 blobs; None when incomparable
    (missing blob, or dimension mismatch after an embedding-provider switch)."""
    if not a or not b or len(a) != len(b):
        return None
    n = len(a) // 4
    va = struct.unpack(f'{n}f', a)
    vb = struct.unpack(f'{n}f', b)
    dot = sum(x * y for x, y in zip(va, vb))
    norm = math.sqrt(sum(x * x for x in va)) * math.sqrt(sum(y * y for y in vb))
    if norm == 0:
        return None
    return dot / norm


def find_similar_answer(embedding: bytes, exclude_id: str) -> tuple[dict, float] | None:
    """Best cosine match among active cards, or None. Brute force is fine at
    personal-deck scale."""
    rows = get_db().execute(
        "SELECT id, question, answer, answer_embedding FROM learning_cards"
        " WHERE state='active' AND answer_embedding IS NOT NULL AND id != ?",
        (exclude_id,),
    ).fetchall()
    best, best_score = None, 0.0
    for row in rows:
        score = cosine(embedding, row['answer_embedding'])
        if score is not None and score > best_score:
            best, best_score = row, score
    if best is None:
        return None
    return {'id': best['id'], 'question': best['question'], 'answer': best['answer']}, best_score
