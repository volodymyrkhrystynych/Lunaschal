"""Notes to self: freeform notes jotted from chat (backend/delegate/tools.py's
create_note_to_self), resurfaced for review on a fixed ladder capped at 14
days. Not FSRS: a note carries no correctness signal to grade, just
seen-or-not, and FSRS's whole value is fitting long-tail memory decay —
exactly the growth-without-limit behavior a 14-day ceiling exists to
suppress. Dismissing a note just advances it to the next rung; editing its
text never touches the schedule.
"""
import time

from ulid import ULID

from backend.db.connection import get_db, row_to_dict

LADDER = (1, 2, 4, 7, 14)
DAY_SECONDS = 86400
MAX_CONTENT_CHARS = 4000


def next_interval_days(current: int) -> int:
    """The next rung after `current`, capped at the ladder's last value."""
    for step in LADDER:
        if step > current:
            return step
    return LADDER[-1]


def create_note(content: str, now: int | None = None) -> str:
    content = content.strip()[:MAX_CONTENT_CHARS]
    if not content:
        raise ValueError('a note needs content')
    now = now if now is not None else int(time.time())
    note_id = str(ULID())
    interval = LADDER[0]
    db = get_db()
    db.execute(
        'INSERT INTO notes_to_self'
        ' (id, content, interval_days, due, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?)',
        (note_id, content, interval, now + interval * DAY_SECONDS, now, now),
    )
    db.commit()
    return note_id


def get_note(note_id: str) -> dict | None:
    row = get_db().execute(
        'SELECT * FROM notes_to_self WHERE id=?', (note_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def list_due(now: int | None = None, limit: int = 20) -> list[dict]:
    now = now if now is not None else int(time.time())
    rows = get_db().execute(
        'SELECT * FROM notes_to_self WHERE due<=? ORDER BY due LIMIT ?',
        (now, limit),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def dismiss_note(note_id: str, now: int | None = None) -> dict | None:
    """Advances the note to the next ladder rung, due counted from now — the
    same reschedule-from-actual-review-time behavior backend/learning/scheduler.py
    gets from FSRS, so a note left overdue doesn't pile several rungs at once."""
    db = get_db()
    row = db.execute(
        'SELECT interval_days FROM notes_to_self WHERE id=?', (note_id,)
    ).fetchone()
    if not row:
        return None
    now = now if now is not None else int(time.time())
    interval = next_interval_days(row['interval_days'])
    db.execute(
        'UPDATE notes_to_self SET interval_days=?, due=?, updated_at=? WHERE id=?',
        (interval, now + interval * DAY_SECONDS, now, note_id),
    )
    db.commit()
    return get_note(note_id)


def edit_note(note_id: str, content: str, now: int | None = None) -> dict | None:
    """Copy-on-write: the revision row holds the text as it stood *before*
    this edit, the same shape user_memory_revisions uses. The review schedule
    is untouched — editing a note isn't reviewing it."""
    content = content.strip()[:MAX_CONTENT_CHARS]
    if not content:
        raise ValueError('a note needs content')
    db = get_db()
    row = db.execute(
        'SELECT content FROM notes_to_self WHERE id=?', (note_id,)
    ).fetchone()
    if not row:
        return None
    now = now if now is not None else int(time.time())
    if row['content'] != content:
        db.execute(
            'INSERT INTO note_to_self_revisions (id, note_id, content, created_at)'
            ' VALUES (?,?,?,?)',
            (str(ULID()), note_id, row['content'], now),
        )
        db.execute(
            'UPDATE notes_to_self SET content=?, updated_at=? WHERE id=?',
            (content, now, note_id),
        )
        db.commit()
    return get_note(note_id)


def list_revisions(note_id: str) -> list[dict]:
    rows = get_db().execute(
        'SELECT * FROM note_to_self_revisions WHERE note_id=? ORDER BY created_at DESC',
        (note_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]
