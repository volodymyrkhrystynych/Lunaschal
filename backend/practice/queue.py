"""Session selection for the Practice tab.

Freeform weighted drill, not spaced repetition: no due dates. A session pulls
a batch of snippets weighted toward ones never attempted, typed inaccurately,
or typed slowly — and, among otherwise-equal snippets, ones not practiced
recently. Pure and DB-free so it can be unit tested with fabricated progress
dicts; the caller (backend/routes/practice.py) is responsible for loading
`progress_by_id` from `practice_progress` and passing `now`.
"""

import random
import time

NEW_BONUS = 1000.0
SPEED_WEIGHT = 0.5
TARGET_WPM = 40.0
RECENCY_WEIGHT = 2.0
RECENCY_CAP_DAYS = 14.0
DEFAULT_SIZE = 10


def _score(progress: dict | None, now: int) -> float:
    if not progress or not progress.get('attempts_count'):
        return NEW_BONUS
    accuracy_penalty = max(0.0, 100.0 - (progress.get('last_accuracy') or 0.0))
    speed_penalty = max(0.0, TARGET_WPM - (progress.get('last_wpm') or 0.0)) * SPEED_WEIGHT
    last_practiced_at = progress.get('last_practiced_at')
    days_since = max(0.0, (now - last_practiced_at) / 86400) if last_practiced_at else 0.0
    recency_bonus = min(days_since, RECENCY_CAP_DAYS) * RECENCY_WEIGHT
    return accuracy_penalty + speed_penalty + recency_bonus


def build_session(
    snippets: list[dict],
    progress_by_id: dict[str, dict],
    now: int | None = None,
    size: int = DEFAULT_SIZE,
    language: str | None = None,
    category: str | None = None,
    rng: random.Random | None = None,
) -> list[str]:
    """Return an ordered list of snippet ids for a drill session."""
    now = now if now is not None else int(time.time())
    rng = rng if rng is not None else random
    pool = [
        s
        for s in snippets
        if (language is None or s['language'] == language)
        and (category is None or s['category'] == category)
    ]
    ranked = sorted(pool, key=lambda s: _score(progress_by_id.get(s['id']), now), reverse=True)
    ids = [s['id'] for s in ranked[:size]]
    rng.shuffle(ids)
    return ids
