"""One save path for a chapter or a note, and how two devices stay honest.

The phone keeps a book on disk and may send a save hours after it was written,
against text the desktop has since changed. `updated_at` cannot catch that: it
has one-second resolution, so two saves in a second compare equal. Each document
therefore carries an integer `revision` that every text or title write
increments, and a client that names the revision it edited gets a Conflict
rather than silently replacing what it never saw.

A client that names none gets the old last-write-wins, so an older build keeps
working. Its write still moves `revision`, which is what lets every other
client notice it.

The check and the write have to be one step, and this app shares one SQLite
connection across its request threads, so a rollback here could undo another
request's work. Hence the lock rather than a transaction, the same answer
`backend/routes/chat.py` gives for resolving a proposal.
"""
import threading

DOCUMENT_TABLES = ('writing_chapters', 'writing_context_docs')

LOCK = threading.RLock()


class DocumentMissing(Exception):
    """The document was deleted, so a save has nowhere to go."""


class DocumentConflict(Exception):
    """The base revision is out of date. `current` is the row as it stands."""

    def __init__(self, current):
        super().__init__('document changed')
        self.current = current


def save(db, table: str, doc_id: str, text: dict, *, extra: dict | None = None,
         base_revision: int | None, before_write, now: int) -> tuple:
    """Write `text` (title/content columns) and `extra` (anything else) to one
    document. Returns (the row before the write, the revision after it).

    `before_write` runs only for a real text change, so a pin never leaves a
    revision behind. Call under LOCK. The caller commits.

    A retry is not a conflict: a response lost on the way back makes the client
    send the same words against the old base, and answering 409 would show
    someone a conflict with their own text.
    """
    if table not in DOCUMENT_TABLES:
        raise ValueError(f'not a revisioned table: {table}')
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (doc_id,)).fetchone()
    if row is None:
        raise DocumentMissing(doc_id)
    extra = extra or {}
    changes_text = any(row[column] != value for column, value in text.items())
    if text and not changes_text:
        text = {}
    elif text and base_revision is not None and base_revision != row['revision']:
        raise DocumentConflict(row)

    revision = row['revision']
    updates = dict(extra)
    if text:
        before_write(db, doc_id, now)
        revision += 1
        updates.update(text, revision=revision, updated_at=now)
    if updates:
        columns = ', '.join(f'{column}=?' for column in updates)
        db.execute(f'UPDATE {table} SET {columns} WHERE id=?', [*updates.values(), doc_id])
    return row, revision


def base_revision_of(body: dict) -> int | None:
    """The `baseRevision` of a request, or None. Raises ValueError on anything
    that is not a whole number, so a malformed base cannot pass as "no base"
    and quietly skip the check."""
    value = body.get('baseRevision')
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('baseRevision must be a non-negative integer')
    return value
