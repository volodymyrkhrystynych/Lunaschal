"""Deferred grading of a saved review attempt.

An attempt is written to the DB the instant the user submits an answer, so the
session survives leaving the view; the AI grade lands on the row afterwards.
The work runs on the `llm_jobs` queue (`backend/ai/jobs.py`), which is what
keeps it below chat in priority: chat generates inline on a request thread,
while every background grade queues behind one shared worker.
"""
import json
import time

from backend.db.connection import build_update, get_db


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
    db = get_db()
    build_update(db, 'learning_attempts', cols, 'id=?', (attempt_id,))
    db.commit()


def _hear_clip(attempt) -> str:
    """Transcribe a spoken answer's waiting clip into the attempt's answer.

    Committed before grading starts, and the clip deleted only then: a grade
    that is interrupted re-runs on the saved words instead of the audio, and a
    transcription that fails keeps the clip (rating the card removes it).
    Returns the answer as it now stands.
    """
    from backend.learning import storage
    from backend.routes import stt as stt_routes
    answer = (attempt['answer'] or '').strip()
    clip = storage.find_clip(attempt['id'])
    if clip is None:
        return answer
    text = stt_routes.transcribe_file(clip)
    answer = f'{answer} {text}' if answer else text
    _finish(attempt['id'], answer=answer)
    storage.delete_attempt_dir(attempt['id'])
    return answer


def grade_attempt(attempt_id: str) -> None:
    """Transcribe a spoken clip → voice-normalize → embedding gate → cached
    claims → coverage."""
    from backend.ai import learning_grading
    from backend.learning.dedup import cosine, embed_answer
    db = get_db()
    try:
        attempt = db.execute(
            'SELECT * FROM learning_attempts WHERE id=?', (attempt_id,)
        ).fetchone()
        if not attempt or attempt['mode'] != 'answered':
            return
        answer = _hear_clip(attempt)
        card = db.execute(
            'SELECT * FROM learning_cards WHERE id=?', (attempt['card_id'],)
        ).fetchone()
        if not card:
            return
        if not answer:
            # A spoken answer whose clip is gone and never became words: there
            # is nothing to grade, and pending would be polled forever.
            _finish(attempt_id, grade_status='error')
            return

        if attempt['answer_mode'] == 'voice':
            from backend.ai.learning_generation import normalize_transcript
            answer = normalize_transcript(answer)

        speech = bool(attempt['speech_requested'])

        # Cheap gate: an answer nowhere near the stored one is graded Again
        # without the claim-check LLM call.
        coverage = None
        if card['answer_embedding'] is not None:
            sim = cosine(embed_answer(answer), card['answer_embedding'])
            if sim is not None and sim < learning_grading.GATE_LOW:
                coverage = learning_grading.gated_coverage(speech=speech)

        if coverage is None:
            coverage = learning_grading.check_coverage(card_claims(card), answer, speech=speech)

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
