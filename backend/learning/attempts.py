"""Deferred grading of a saved review attempt.

An attempt is written to the DB the instant the user submits an answer, so the
session survives leaving the view; the AI grade lands on the row afterwards.
The work runs on `backend.ai.background`'s single-worker executor, which is what
keeps it below chat in priority: chat generates inline on a request thread,
while every background grade queues behind one shared worker.
"""
import json
import time

from backend.db.connection import get_db


def card_claims(row) -> list[dict]:
    """Cached claim decomposition; computed at first grade, stored on the card."""
    from backend.ai.learning_grading import decompose_claims
    if row['claims']:
        return json.loads(row['claims'])
    claims = decompose_claims(row['question'], row['answer'])
    db = get_db()
    db.execute('UPDATE learning_cards SET claims=? WHERE id=?', (json.dumps(claims), row['id']))
    db.commit()
    return claims


def _finish(attempt_id: str, **cols) -> None:
    """Write the outcome back. The attempt may be gone — the user can rate a
    card (which deletes it) before its grade lands — and that's a no-op."""
    cols['updated_at'] = int(time.time())
    set_clause = ', '.join(f'{k}=?' for k in cols)
    db = get_db()
    db.execute(f'UPDATE learning_attempts SET {set_clause} WHERE id=?',
               [*cols.values(), attempt_id])
    db.commit()


def grade_attempt(attempt_id: str) -> None:
    """Voice-normalize → embedding gate → cached claims → coverage."""
    from backend.ai import learning_grading
    from backend.learning.dedup import cosine, embed_answer
    db = get_db()
    try:
        attempt = db.execute(
            'SELECT * FROM learning_attempts WHERE id=?', (attempt_id,)
        ).fetchone()
        if not attempt or attempt['mode'] != 'answered':
            return
        answer = (attempt['answer'] or '').strip()
        card = db.execute(
            'SELECT * FROM learning_cards WHERE id=?', (attempt['card_id'],)
        ).fetchone()
        if not answer or not card:
            return

        if attempt['answer_mode'] == 'voice':
            from backend.ai.learning_generation import normalize_transcript
            answer = normalize_transcript(answer)

        # Cheap gate: an answer nowhere near the stored one is graded Again
        # without the claim-check LLM call.
        coverage = None
        if card['answer_embedding'] is not None:
            sim = cosine(embed_answer(answer), card['answer_embedding'])
            if sim is not None and sim < learning_grading.GATE_LOW:
                coverage = learning_grading.gated_coverage()

        if coverage is None:
            coverage = learning_grading.check_coverage(card_claims(card), answer)

        _finish(
            attempt_id,
            grade_status='done',
            coverage=json.dumps(coverage),
            suggested_rating=learning_grading.suggest_rating(coverage),
            normalized_answer=answer,
        )
    except Exception as e:
        print(f'Background grade failed for attempt {attempt_id}: {e}')
        try:
            _finish(attempt_id, grade_status='error')
        except Exception:
            pass
