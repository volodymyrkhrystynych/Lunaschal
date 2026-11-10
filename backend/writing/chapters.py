"""A writing project's chapters: history, daily word counts, accepted proposals.

Chapters keep the history notes do (`backend/writing/notes.py`) now that more
than one device writes to them. `content` on a revision is the chapter as it
stood before the change. Edits coalesce over five minutes rather than a note's
one: a chapter is the long document, and it is saved every few seconds for as
long as someone is writing.

An accepted proposal is the model's text, so it never counts as words written
and is never a whole-chapter rewrite: `backend/writing/proposals.py` explains
why a local model is not trusted with that.
"""
import time

from ulid import ULID

from backend.day_boundary import day_key_for
from backend.writing import revisioned

EDIT_COALESCE_SECONDS = 300


class ChapterRejected(ValueError):
    """A chapter write that failed validation. The message is shown to the user."""


def word_count(text: str | None) -> int:
    """Whitespace-separated words, the count every client shows."""
    return len((text or '').split())


def snapshot(db, chapter_id: str, *, source: str, message_id: str | None = None,
             now: int | None = None) -> str | None:
    """Record the chapter as it stands, before a change. Returns the revision
    id, or None when nothing was written (no such chapter, or a coalesced edit)."""
    now = now or int(time.time())
    row = db.execute('SELECT title, content FROM writing_chapters WHERE id=?',
                     (chapter_id,)).fetchone()
    if row is None:
        return None
    if source == 'edit':
        last = db.execute(
            'SELECT source, created_at FROM writing_chapter_revisions'
            ' WHERE chapter_id=? ORDER BY created_at DESC, id DESC LIMIT 1',
            (chapter_id,),
        ).fetchone()
        if (last is not None and last['source'] == 'edit'
                and now - last['created_at'] < EDIT_COALESCE_SECONDS):
            return None
    revision_id = str(ULID())
    db.execute(
        'INSERT INTO writing_chapter_revisions'
        '(id, chapter_id, title, content, source, message_id, created_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (revision_id, chapter_id, row['title'], row['content'], source, message_id, now),
    )
    return revision_id


def restore(db, chapter_id: str, revision_id: str) -> bool:
    """Put a revision back, recording the current state first so a restore is
    itself undoable. False when either does not exist."""
    with revisioned.LOCK:
        revision = db.execute(
            'SELECT title, content FROM writing_chapter_revisions WHERE id=? AND chapter_id=?',
            (revision_id, chapter_id),
        ).fetchone()
        if revision is None:
            return False
        now = int(time.time())
        if snapshot(db, chapter_id, source='restore', now=now) is None:
            return False
        db.execute(
            'UPDATE writing_chapters SET title=?, content=?, revision=revision+1, updated_at=?'
            ' WHERE id=?',
            (revision['title'], revision['content'], now, chapter_id),
        )
        db.execute(
            'UPDATE writing_projects SET updated_at=? WHERE id ='
            ' (SELECT project_id FROM writing_chapters WHERE id=?)',
            (now, chapter_id),
        )
    return True


def record_words(db, project_id: str, before: str | None, after: str | None, at: int) -> None:
    """Add a save's net change to the day it was written. Net, so that cutting
    a paragraph takes it back off, and signed, so that a day of editing can
    legitimately end below zero. The 4am day of `at`, not of now: a draft
    written offline counts on the day it was written."""
    delta = word_count(after) - word_count(before)
    if delta:
        db.execute(
            'INSERT INTO writing_daily_words(project_id, day, words) VALUES (?,?,?)'
            ' ON CONFLICT(project_id, day) DO UPDATE SET words = words + excluded.words',
            (project_id, day_key_for(at), delta),
        )


def apply_update(db, project_id: str, *, chapter_id: str, mode: str, content: str,
                 find: str | None = None, message_id: str | None = None) -> dict:
    """Add to a chapter's end or replace one quoted passage, as an accepted proposal.

    There is deliberately no revision check. The author is often typing in the
    chapter while discussing it, which would make every card stale. The quoted
    passage is the real guard: it must still appear exactly once, so an accept
    can only touch the text the card showed. An append touches nothing at all.
    """
    content = (content or '').strip()
    if not content:
        raise ChapterRejected('nothing to add')
    if mode not in ('append', 'replace_passage'):
        raise ChapterRejected("mode must be 'append' or 'replace_passage'")
    with revisioned.LOCK:
        row = db.execute('SELECT project_id, content FROM writing_chapters WHERE id=?',
                         (chapter_id,)).fetchone()
        if row is None or row['project_id'] != project_id:
            raise ChapterRejected('that chapter no longer exists in this project')
        current = row['content'] or ''
        if mode == 'append':
            new = f'{current.rstrip()}\n\n{content}' if current.strip() else content
        else:
            passage = find or ''
            matches = current.count(passage) if passage else 0
            if matches == 0:
                raise ChapterRejected('that passage is no longer in the chapter; '
                                      'dismiss this and ask again')
            if matches > 1:
                raise ChapterRejected('that passage appears more than once in the '
                                      'chapter, so quote more of it')
            new = current.replace(passage, content, 1)
        now = int(time.time())
        revision_id = snapshot(db, chapter_id, source='accept', message_id=message_id, now=now)
        db.execute(
            'UPDATE writing_chapters SET content=?, revision=revision+1, updated_at=? WHERE id=?',
            (new, now, chapter_id),
        )
        db.execute('UPDATE writing_projects SET updated_at=? WHERE id=?', (now, project_id))
    return {'id': chapter_id, 'revisionId': revision_id}
