"""A writing project's notes, and the copy-on-write history behind them.

Notes are `writing_context_docs` rows (the table name is legacy). They used to
be written only by the author's own editor and by the Summarize button, so an
overwrite was always something the author had just done themselves. Accepting a
discussion's proposed update is different: the author approved a card, but the
text that replaced their note was written by a model. So every change that can
lose text leaves a revision behind, the `user_memory_revisions` pattern —
`content` on a revision is the note as it stood *before* the change, which is
exactly what Restore puts back.

The editor autosaves every 1.5 s, and a revision per keystroke burst would bury
the one revision that matters (the state before an accepted proposal) under
hundreds of near-identical ones. `snapshot(..., source='edit')` therefore
coalesces: an edit within `EDIT_COALESCE_SECONDS` of an earlier *edit* revision
does not write another. Accepts and restores never coalesce.
"""
import time

from ulid import ULID

from backend.db.connection import get_db

DOC_TYPES = ('character', 'outline', 'worldbuilding', 'note')

EDIT_COALESCE_SECONDS = 60

# One note is read into the discussion's prompt whole when pinned, so it has a
# ceiling; a proposal larger than this is refused where the model can read why.
MAX_NOTE_CHARS = 20_000


class NoteRejected(ValueError):
    """A note write that failed validation. The message is shown to the user."""


def normalize_doc_type(value) -> str:
    value = (value or 'note').strip().lower() if isinstance(value, str) else 'note'
    return value if value in DOC_TYPES else 'note'


def project_for_message(db, message_id: str | None) -> str | None:
    """The writing project a chat message belongs to, or None.

    The one place an accept handler learns which project it is writing into.
    Never from the card's data: the card is editable, and a project id on it
    would be a field that moves a note into another book.
    """
    if not message_id:
        return None
    row = db.execute(
        'SELECT c.writing_project_id AS pid FROM messages m'
        ' JOIN conversations c ON c.id = m.conversation_id WHERE m.id = ?',
        (message_id,),
    ).fetchone()
    return row['pid'] if row else None


def snapshot(db, doc_id: str, *, source: str, message_id: str | None = None,
             now: int | None = None) -> str | None:
    """Record the note as it stands right now, before a change. Returns the
    revision id, or None when nothing was written (no such note, or an edit
    coalesced into the previous one)."""
    now = now or int(time.time())
    row = db.execute(
        'SELECT title, content FROM writing_context_docs WHERE id=?', (doc_id,)
    ).fetchone()
    if row is None:
        return None
    if source == 'edit':
        last = db.execute(
            'SELECT source, created_at FROM writing_context_doc_revisions'
            ' WHERE doc_id=? ORDER BY created_at DESC, id DESC LIMIT 1',
            (doc_id,),
        ).fetchone()
        if (last is not None and last['source'] == 'edit'
                and now - last['created_at'] < EDIT_COALESCE_SECONDS):
            return None
    rev_id = str(ULID())
    db.execute(
        'INSERT INTO writing_context_doc_revisions'
        '(id, doc_id, title, content, source, message_id, created_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (rev_id, doc_id, row['title'], row['content'], source, message_id, now),
    )
    return rev_id


def create(db, project_id: str, *, title: str, content: str, doc_type: str) -> str:
    title = (title or '').strip()
    if not title:
        raise NoteRejected('title required')
    content = content or ''
    if len(content) > MAX_NOTE_CHARS:
        raise NoteRejected(f'note is longer than {MAX_NOTE_CHARS:,} characters')
    if not db.execute('SELECT 1 FROM writing_projects WHERE id=?', (project_id,)).fetchone():
        raise NoteRejected('that writing project no longer exists')
    now = int(time.time())
    note_id = str(ULID())
    db.execute(
        'INSERT INTO writing_context_docs'
        '(id, project_id, title, content, doc_type, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (note_id, project_id, title, content, normalize_doc_type(doc_type), now, now),
    )
    db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, project_id))
    return note_id


def apply_update(db, project_id: str, *, note_id: str, mode: str, content: str,
                 base_updated_at=None, message_id: str | None = None) -> dict:
    """Append to or replace a note's body, as an accepted proposal.

    Refuses when the note has been edited since the proposal was staged: the
    card was built against text that no longer exists, and appending to or
    replacing what the author has since written is exactly the silent
    overwrite the revision table is there to make recoverable — better not to
    need it.
    """
    content = (content or '').strip()
    if not content:
        raise NoteRejected('nothing to add')
    if mode not in ('append', 'replace'):
        raise NoteRejected("mode must be 'append' or 'replace'")
    row = db.execute(
        'SELECT project_id, content, updated_at FROM writing_context_docs WHERE id=?',
        (note_id,),
    ).fetchone()
    if row is None or row['project_id'] != project_id:
        raise NoteRejected('that note no longer exists in this project')
    if (isinstance(base_updated_at, int) and not isinstance(base_updated_at, bool)
            and row['updated_at'] > base_updated_at):
        raise NoteRejected('the note was edited after this was proposed; '
                           'dismiss it and ask again')

    current = row['content'] or ''
    new = content if mode == 'replace' else (
        f'{current.rstrip()}\n\n{content}' if current.strip() else content)
    if len(new) > MAX_NOTE_CHARS:
        raise NoteRejected(f'note would be longer than {MAX_NOTE_CHARS:,} characters')

    now = int(time.time())
    revision_id = snapshot(db, note_id, source='accept', message_id=message_id, now=now)
    db.execute(
        'UPDATE writing_context_docs SET content=?, updated_at=? WHERE id=?',
        (new, now, note_id),
    )
    db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, project_id))
    return {'id': note_id, 'revisionId': revision_id}


def restore(db, note_id: str, revision_id: str) -> bool:
    """Put a revision back, recording the current state first so a restore is
    itself undoable. False when either does not exist."""
    rev = db.execute(
        'SELECT title, content FROM writing_context_doc_revisions WHERE id=? AND doc_id=?',
        (revision_id, note_id),
    ).fetchone()
    if rev is None:
        return False
    now = int(time.time())
    if snapshot(db, note_id, source='restore', now=now) is None:
        return False
    db.execute(
        'UPDATE writing_context_docs SET title=?, content=?, updated_at=? WHERE id=?',
        (rev['title'], rev['content'], now, note_id),
    )
    return True
