"""FSRS scheduling adapter.

Cards store their py-fsrs state as a JSON blob in learning_cards.fsrs_state;
NULL means "never reviewed" and is materialized as a fresh Card at review time.
A scheduling reset (new semantic card version) is simply fsrs_state=NULL +
due=now — py-fsrs has no forget primitive, and a fresh Card is exactly the
due-now, short-ramping-intervals behavior we want.
"""
import json
from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler

_scheduler = Scheduler()


def review(
    fsrs_state: str | None,
    rating: int,
    now: datetime | None = None,
) -> tuple[str, int, str]:
    """Apply a 1-4 rating; returns (new_state_json, due_unix, review_log_json)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if fsrs_state:
        card = Card.from_dict(json.loads(fsrs_state))
    else:
        card = Card(due=now)
    card, log = _scheduler.review_card(card, Rating(rating), now)
    return (
        json.dumps(card.to_dict()),
        int(card.due.timestamp()),
        json.dumps(log.to_dict()),
    )


def stability(fsrs_state: str | None) -> float | None:
    if not fsrs_state:
        return None
    return json.loads(fsrs_state).get('stability')
