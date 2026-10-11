"""Review cards: a journal entry the user wrote, scheduled for re-reading.

A review card has no back side. Its "question" is the entry's pages, read in
order, and the only thing to do with it is say how well it was remembered,
so it is created active (there is nothing generated to approve), never
graded, and never revised. It lives in the For review folder, which the app
owns, and comes due with every other card.

The card stores the entry id and nothing of the entry itself: the pages are
read off `journal_attachments` each time it is shown, so pages that upload
after the create (they always do; they need the entry's id) and the title the
metadata pass writes later both appear without anything being copied.
"""
import sqlite3
import time

from ulid import ULID

from backend.day_boundary import day_bounds, day_key_for

REVIEW_FOLDER_KEY = 'review'
REVIEW_FOLDER_NAME = 'For review'
SOURCE_TYPE = 'journal_review'


def ensure_review_folder(db: sqlite3.Connection) -> str:
    """The For review folder's id, adopting a same-named folder or creating it.
    The caller commits."""
    row = db.execute('SELECT id FROM learning_folders WHERE system_key=?',
                     (REVIEW_FOLDER_KEY,)).fetchone()
    if row:
        return row[0]
    row = db.execute('SELECT id FROM learning_folders WHERE lower(name)=lower(?)',
                     (REVIEW_FOLDER_NAME,)).fetchone()
    if row:
        db.execute('UPDATE learning_folders SET system_key=? WHERE id=?',
                   (REVIEW_FOLDER_KEY, row[0]))
        return row[0]
    id = str(ULID())
    now = int(time.time())
    db.execute(
        'INSERT INTO learning_folders(id, name, position, system_key, created_at, updated_at)'
        ' VALUES (?,?,COALESCE((SELECT MAX(position)+1 FROM learning_folders), 0),?,?,?)',
        (id, REVIEW_FOLDER_NAME, REVIEW_FOLDER_KEY, now, now),
    )
    return id


def first_due(now: int) -> int:
    """The next 4am: what was just written is first worth recalling tomorrow."""
    return day_bounds(day_key_for(now))[1]


def ensure_review_card(db: sqlite3.Connection, entry_id: str, *, now: int | None = None) -> None:
    """Makes `entry_id`'s review card unless it has one. Idempotent, so a
    replayed create is a no-op. The caller commits."""
    now = int(time.time()) if now is None else now
    folder_id = ensure_review_folder(db)
    db.execute(
        'INSERT OR IGNORE INTO learning_cards'
        ' (id, folder_id, question, answer, state, kind, source_type, source_id,'
        '  due, created_at, updated_at)'
        " VALUES (?,?,?,'','active','review',?,?,?,?,?)",
        (str(ULID()), folder_id, REVIEW_FOLDER_NAME, SOURCE_TYPE, entry_id,
         first_due(now), now, now),
    )


def carry_review_card(db: sqlite3.Connection, old_entry_ids: list[str], new_entry_id: str) -> None:
    """A re-filed notebook stands in for the entry it replaces, and so does its
    card: moved rather than recreated, the schedule survives the re-file. The
    old entry's delete would otherwise take the card with it (the trigger in
    schema.sql). At most one moves; the caller commits."""
    for old in old_entry_ids:
        if old == new_entry_id:
            continue
        moved = db.execute(
            'UPDATE OR IGNORE learning_cards SET source_id=?, updated_at=?'
            ' WHERE source_type=? AND source_id=?',
            (new_entry_id, int(time.time()), SOURCE_TYPE, old),
        )
        if moved.rowcount:
            return


def review_payloads(db: sqlite3.Connection, entry_ids: list[str]) -> dict[str, dict]:
    """`{entry_id: {entryId, title, pages}}` for the entries given, each page
    an image attachment in the order the entry shows them."""
    if not entry_ids:
        return {}
    marks = ','.join('?' * len(entry_ids))
    out = {
        row['id']: {'entryId': row['id'], 'title': row['title'], 'pages': []}
        for row in db.execute(f'SELECT id, title FROM journal_entries WHERE id IN ({marks})', entry_ids)
    }
    rows = db.execute(
        f"SELECT id, entry_id, name FROM journal_attachments WHERE entry_id IN ({marks})"
        " AND kind='image' AND path != '' ORDER BY position, created_at",
        entry_ids,
    )
    for row in rows:
        out[row['entry_id']]['pages'].append({
            'id': row['id'], 'name': row['name'],
            'url': f"/api/journal/attachments/{row['id']}/file",
        })
    return out
