"""Daily context requests for media-only entries. No model or scheduler needed."""
import time
from datetime import date, timedelta
from threading import RLock

from backend.day_boundary import day_bounds, day_key_for

lock = RLock()

# Image captions live in transcript too; only AUDIO speech supplies context.
# Descriptions and generated titles never disqualify an otherwise bare entry.
_BLANK = "trim(COALESCE({}, ''), char(9) || char(10) || char(13) || ' ') = ''"
_ELIGIBLE = f"""
    e.context_prompt_status = 'pending'
    AND {_BLANK.format('e.content')}
    AND {_BLANK.format('e.raw_content')}
    AND EXISTS (SELECT 1 FROM journal_attachments a WHERE a.entry_id=e.id)
    AND NOT EXISTS (
        SELECT 1 FROM journal_attachments a WHERE a.entry_id=e.id
        AND (a.kind NOT IN ('image', 'audio') OR
             (a.kind='audio' AND (NOT ({_BLANK.format('a.transcript')})
                                  OR a.transcript_status='running')))
    )
"""

# How long one of today's entries must sit before it is asked about. An entry
# is briefly wordless on its way to having words: a recording is committed
# before its transcription is marked running, and a second photo or clip can
# land a request later.
TODAY_SETTLE_SECONDS = 120

# Today's entries that are still arriving or still being sorted. A screenshot
# session is open while images keep landing on it; a Capture-tab Save
# (`auto_file`) may yet move to the food log or the spending ledger, and has
# stayed in the journal only once its `journal.autofile` job has finished — or
# once plain metadata was queued instead, which is the waiter giving up
# (`_generate_metadata_bg` in `backend/routes/journal.py`).
_TODAY_SETTLED = """
    e.created_at<=?
    AND NOT EXISTS (SELECT 1 FROM journal_attachments a WHERE a.entry_id=e.id AND a.created_at>?)
    AND NOT EXISTS (SELECT 1 FROM journal_screenshot_sessions s WHERE s.entry_id=e.id AND s.is_open=1)
    AND (e.auto_file=0 OR EXISTS (
        SELECT 1 FROM llm_jobs j WHERE j.target_id=e.id
        AND ((j.kind='journal.autofile' AND j.status IN ('done', 'error'))
             OR j.kind='journal.metadata')))
"""


def prepare(db, *, now=None, include_today=False):
    """The day's frozen backlog slots, after today's waiting entries if asked.

    `include_today` is the phone's Daily page, which asks about today's while
    the day is still remembered; the web's Chat cards leave it off and see the
    backlog alone. Today's are uncapped, take no slot, are never frozen, and
    are listed only once settled (`_TODAY_SETTLED`). Left unanswered, they are
    yesterday's tomorrow, which joins the backlog uncapped like any
    late-arriving capture. Backlog slots count answered/dismissed slots too.
    Older unfinished prompts rejoin the backlog next day, newest first. Merely
    closing a modal has no effect on eligibility.
    """
    now = int(time.time()) if now is None else now
    today = day_key_for(now)
    today_start, today_end = day_bounds(today)
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    start, end = day_bounds(yesterday)
    with lock:
        db.execute(
            f'UPDATE journal_entries AS e SET context_prompt_day=? WHERE {_ELIGIBLE}'
            ' AND e.created_at>=? AND e.created_at<?'
            ' AND COALESCE(e.context_prompt_day, \'\')<>?', (today, start, end, today),
        )
        yesterday_count = db.execute(
            'SELECT COUNT(*) FROM journal_entries WHERE context_prompt_day=?'
            ' AND created_at>=? AND created_at<?', (today, start, end),
        ).fetchone()[0]
        # A late upload from yesterday takes precedence over an older slot.
        db.execute(
            'UPDATE journal_entries SET context_prompt_day=NULL WHERE id IN ('
            'SELECT id FROM journal_entries WHERE context_prompt_day=? AND created_at<?'
            ' ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?)',
            (today, start, max(0, 3 - yesterday_count)),
        )
        chosen = db.execute(
            'SELECT COUNT(*) FROM journal_entries WHERE context_prompt_day=?',
            (today,),
        ).fetchone()[0]
        if chosen < 3:
            db.execute(
                'UPDATE journal_entries SET context_prompt_day=? WHERE id IN ('
                f'SELECT e.id FROM journal_entries e WHERE {_ELIGIBLE}'
                ' AND e.created_at<? AND COALESCE(e.context_prompt_day, \'\')<>?'
                ' ORDER BY e.created_at DESC, e.id DESC LIMIT ?)',
                (today, start, today, 3 - chosen),
            )
        db.commit()
        todays = db.execute(
            f'SELECT e.* FROM journal_entries e WHERE {_ELIGIBLE} AND {_TODAY_SETTLED}'
            ' AND e.created_at>=? AND e.created_at<?'
            ' ORDER BY e.created_at DESC, e.id DESC',
            (now - TODAY_SETTLE_SECONDS, now - TODAY_SETTLE_SECONDS, today_start, today_end),
        ).fetchall() if include_today else []
        return todays + db.execute(
            f'SELECT e.* FROM journal_entries e WHERE {_ELIGIBLE}'
            ' AND e.context_prompt_day=? AND e.created_at<?'
            ' ORDER BY e.created_at DESC, e.id DESC', (today, end),
        ).fetchall()
