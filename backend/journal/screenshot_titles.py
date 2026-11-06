"""Naming the screenshot runs the global shortcut files as "Screenshots".

A run of captures (`journal_screenshot_sessions`, see `backend/routes/journal.py`)
lands as one journal entry and, from its second capture, one calendar event,
both titled `DEFAULT_TITLE`. Nothing names them afterwards, so a week of
captures reads as a column of identical blocks. The chat can: it looks the runs
up here with their captions, and proposes a name for each on a confirm card.

Two rules shape the rename:

- **Only the default title is replaced.** The entry and the event are renamed
  independently, each only while it still says `DEFAULT_TITLE`, so a name the
  user typed into either one by hand is never overwritten by the other's.
- **A rename closes the run**, as a manual rename of the entry already does: a
  named entry is user-authored context, and later captures belong in a run of
  their own rather than under a title chosen for different ones.
"""
import time

from backend.recall import clip as _clip

DEFAULT_TITLE = 'Screenshots'
MAX_TITLE_CHARS = 120
DEFAULT_DAYS = 7
MAX_DAYS = 60
MAX_RUNS = 12
# Captions are a model's prose about each picture; a few lines of each is
# enough to name the run, and the whole result is pasted into the transcript.
MAX_CAPTIONS = 6
MAX_CAPTION_CHARS = 160


class RenameRejected(ValueError):
    """A rename that cannot be applied, worded for the person reading the card."""


def _run_row(db, entry_id: str):
    return db.execute(
        'SELECT s.entry_id, s.calendar_event_id, s.first_local_date,'
        ' s.first_local_time, s.last_local_date, s.last_local_time,'
        ' j.title AS entry_title, j.content, e.title AS event_title'
        ' FROM journal_screenshot_sessions s'
        ' JOIN journal_entries j ON j.id = s.entry_id'
        ' LEFT JOIN calendar_events e ON e.id = s.calendar_event_id'
        ' WHERE s.entry_id = ?',
        (entry_id,),
    ).fetchone()


def _is_untitled(row) -> bool:
    return row['entry_title'] == DEFAULT_TITLE or row['event_title'] == DEFAULT_TITLE


def describe_run(db, row) -> dict:
    shots = db.execute(
        "SELECT transcript, transcript_status FROM journal_attachments"
        " WHERE entry_id=? AND kind='image' ORDER BY created_at, id",
        (row['entry_id'],),
    ).fetchall()
    captions = [_clip(s['transcript'], MAX_CAPTION_CHARS) for s in shots
                if s['transcript_status'] == 'done' and (s['transcript'] or '').strip()]
    return {
        'entryId': row['entry_id'],
        'date': row['first_local_date'],
        'time': row['first_local_time'][:5],
        'endDate': row['last_local_date'],
        'endTime': row['last_local_time'][:5],
        'shots': len(shots),
        'captions': captions[:MAX_CAPTIONS],
        'note': _clip(row['content'], MAX_CAPTION_CHARS),
        'hasEvent': row['calendar_event_id'] is not None,
    }


def untitled_runs(db, days: int = DEFAULT_DAYS, *, now: float | None = None) -> list[dict]:
    """Recent runs still carrying the default title on the entry or the event,
    newest first."""
    since = int((now if now is not None else time.time()) - days * 86400)
    rows = db.execute(
        'SELECT s.entry_id, s.calendar_event_id, s.first_local_date,'
        ' s.first_local_time, s.last_local_date, s.last_local_time,'
        ' j.title AS entry_title, j.content, e.title AS event_title'
        ' FROM journal_screenshot_sessions s'
        ' JOIN journal_entries j ON j.id = s.entry_id'
        ' LEFT JOIN calendar_events e ON e.id = s.calendar_event_id'
        ' WHERE s.last_captured_at >= ? AND (j.title = ? OR e.title = ?)'
        ' ORDER BY s.first_captured_at DESC LIMIT ?',
        (since, DEFAULT_TITLE, DEFAULT_TITLE, MAX_RUNS),
    ).fetchall()
    return [describe_run(db, row) for row in rows]


def untitled_run(db, entry_id: str) -> dict | None:
    row = _run_row(db, entry_id)
    return describe_run(db, row) if row is not None and _is_untitled(row) else None


def clean_title(value) -> str:
    title = ' '.join(value.split()) if isinstance(value, str) else ''
    if not title:
        raise RenameRejected('a name is required')
    if len(title) > MAX_TITLE_CHARS:
        raise RenameRejected(f'keep the name under {MAX_TITLE_CHARS} characters')
    if title.casefold() == DEFAULT_TITLE.casefold():
        raise RenameRejected('choose a name other than "Screenshots"')
    return title


def rename_run(db, entry_id: str, title) -> dict:
    """Rename one run's entry and event wherever they still say the default.

    The caller owns the transaction. Raises `RenameRejected` when there is
    nothing left to rename — the run is gone, or both halves were named by
    hand since the card was staged.
    """
    title = clean_title(title)
    row = _run_row(db, entry_id) if isinstance(entry_id, str) else None
    if row is None:
        raise RenameRejected('that screenshot run no longer exists')
    if not _is_untitled(row):
        raise RenameRejected(f'already named "{row["entry_title"] or row["event_title"]}"')
    now = int(time.time())
    if row['entry_title'] == DEFAULT_TITLE:
        db.execute('UPDATE journal_entries SET title=?, updated_at=? WHERE id=?',
                   (title, now, entry_id))
    if row['event_title'] == DEFAULT_TITLE:
        db.execute('UPDATE calendar_events SET title=? WHERE id=?',
                   (title, row['calendar_event_id']))
    db.execute(
        'UPDATE journal_screenshot_sessions SET is_open=0, updated_at=?'
        ' WHERE entry_id=? AND is_open=1',
        (now, entry_id),
    )
    return {'entryId': entry_id, 'title': title,
            'entry': row['entry_title'] == DEFAULT_TITLE,
            'event': row['event_title'] == DEFAULT_TITLE}
