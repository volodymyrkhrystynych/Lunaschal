"""Session selection for the Practice tab.

Freeform weighted drill, not spaced repetition: no due dates. A session
ranks snippets worst-first — weighted toward ones never attempted, typed
inaccurately, or typed slowly, and, among otherwise-equal snippets, ones not
practiced recently — and returns them in that order rather than shuffled, so
the next snippet served is always the one most in need of practice. Pure and
DB-free so it can be unit tested with fabricated progress dicts; the caller
(backend/routes/practice.py) is responsible for loading `progress_by_id`
from `practice_progress` and passing `now`.
"""

import time

NEW_BONUS = 1000.0
SPEED_WEIGHT = 0.5
TARGET_WPM = 40.0
RECENCY_WEIGHT = 2.0
RECENCY_CAP_DAYS = 14.0
# A snippet that couldn't be written from memory is the strongest signal of a
# gap in the bank — it is typed fluently (nothing unlocks blind otherwise) and
# still not known. Weighted above a full accuracy penalty so it outranks any
# snippet that is merely being typed sloppily.
RECALL_FAIL_PENALTY = 120.0
DEFAULT_SIZE = 10


def _score(progress: dict | None, now: int) -> float:
    if not progress or not progress.get('attempts_count'):
        return NEW_BONUS
    accuracy_penalty = max(0.0, 100.0 - (progress.get('last_accuracy') or 0.0))
    speed_penalty = max(0.0, TARGET_WPM - (progress.get('last_wpm') or 0.0)) * SPEED_WEIGHT
    last_practiced_at = progress.get('last_practiced_at')
    days_since = max(0.0, (now - last_practiced_at) / 86400) if last_practiced_at else 0.0
    recency_bonus = min(days_since, RECENCY_CAP_DAYS) * RECENCY_WEIGHT
    # `== 0` and not falsiness: None means never asked from memory, which is not
    # a failure and must not be penalized.
    recall_penalty = RECALL_FAIL_PENALTY if progress.get('last_recall_passed') == 0 else 0.0
    return accuracy_penalty + speed_penalty + recency_bonus + recall_penalty


def build_session(
    snippets: list[dict],
    progress_by_id: dict[str, dict],
    now: int | None = None,
    size: int = DEFAULT_SIZE,
    language: str | None = None,
    category: str | None = None,
) -> list[str]:
    """Return snippet ids ranked worst-first (most in need of practice)."""
    now = now if now is not None else int(time.time())
    pool = [
        s
        for s in snippets
        if (language is None or s['language'] == language)
        and (category is None or s['category'] == category)
    ]
    ranked = sorted(pool, key=lambda s: _score(progress_by_id.get(s['id']), now), reverse=True)
    return [s['id'] for s in ranked[:size]]
