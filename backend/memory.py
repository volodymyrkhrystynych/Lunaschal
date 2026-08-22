"""The one document the assistant keeps up to date about the user.

Chat context is otherwise rebuilt from scratch every turn — the last 24 hours of
journal plus the schedule (`backend/ai/chat.py`) — so nothing the user says in
one conversation survives into the next. This is the store behind the part of
the system prompt that always claimed otherwise.

Its most concrete job is speech-to-text. Dictation mangles proper nouns, and a
name written down here once is a name every feature's own correction pass can
fix afterwards: Journal's Polish (`backend/ai/journal.py`), Ideas' background
cleanup on voice capture (`backend/ai/idea_polish.py`), and the OS-level voice
listener's manual correction route (`backend/routes/stt.py`).

Three things are load-bearing:

- **It is one free-text document, not a list of rows.** The user asked for one
  piece of text, and it goes into the prompt as one block.
- **Every write snapshots the previous document first.** The same reason
  `wiki_revisions` exists: an edit is only safe when it is visible and undoable.
- **The user is the only writer.** Chat used to edit this itself, through a
  `remember`/`revise_memory` pair that saved with no confirmation card — an
  unbidden write on every correction, for something nobody had asked to be made
  permanent. Settings → Memory is the whole write path now.
- **It is capped.** This rides in every chat system prompt, on both the decision
  turn and the answer turn, so an unbounded document is a tax on every message.
  Past the cap `set_memory` refuses rather than silently truncating.
"""
import time

from ulid import ULID

from backend.db.connection import get_db

# ~1,000 tokens, paid twice per turn. Big enough for a page of standing facts,
# small enough that nobody has to think about it.
MAX_CHARS = 4000

# 'remember'/'revise' are what the retired chat tools wrote under; they stay
# valid because the revisions they left behind are still in the table and still
# have to render in Settings.
VALID_SOURCES = {'remember', 'revise', 'user', 'restore'}


class MemoryFull(Exception):
    """The document is at its cap and needs consolidating before it grows."""


def get_memory() -> str:
    row = get_db().execute('SELECT content FROM user_memory WHERE id=1').fetchone()
    return (row['content'] if row else '') or ''


def set_memory(content: str, *, source: str, note: str | None = None) -> str:
    """Replace the document, snapshotting what was there before.

    Returns the stored document. A write that changes nothing is a no-op and
    records no revision, so the history stays a list of actual edits.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f'unknown memory source: {source}')
    content = (content or '').strip()
    if len(content) > MAX_CHARS:
        raise MemoryFull(f'memory is limited to {MAX_CHARS} characters')

    db = get_db()
    previous = get_memory()
    now = int(time.time())
    if content == previous:
        return previous

    db.execute(
        'INSERT INTO user_memory_revisions(id, content, source, note, created_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), previous, source, note, now),
    )
    db.execute(
        'INSERT INTO user_memory(id, content, updated_at) VALUES (1,?,?)'
        ' ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at',
        (content, now),
    )
    db.commit()
    return content


def list_revisions(limit: int = 50) -> list[dict]:
    rows = get_db().execute(
        'SELECT id, content, source, note, created_at FROM user_memory_revisions'
        ' ORDER BY created_at DESC, id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def restore(revision_id: str) -> str | None:
    """Put the document back to what a revision recorded. None if unknown."""
    row = get_db().execute(
        'SELECT content FROM user_memory_revisions WHERE id=?', (revision_id,)
    ).fetchone()
    if row is None:
        return None
    return set_memory(row['content'], source='restore')


def format_memory_context() -> str:
    """The system-prompt block, or '' when there is nothing remembered yet."""
    content = get_memory()
    if not content:
        return ''
    return (
        "Things you have been asked to remember about the user. Treat these as "
        "current fact — in particular, the spellings here are correct, so prefer "
        "them over anything a speech-to-text transcript seems to say. Don't "
        "recite this list back at them:\n\n" + content
    )
