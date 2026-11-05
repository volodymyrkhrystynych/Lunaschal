"""Daily context requests for media-only entries. No model or scheduler needed."""
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


def prepare(db, *, now=None):
    """Freeze the day's backlog slots, counting answered/dismissed slots too.

    Late-arriving captures from yesterday are still added without a cap. Older
    unfinished prompts rejoin the backlog next day, newest first. Merely closing
    a modal has no effect on its eligibility.
    """
    today = day_key_for(now)
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
        return db.execute(
            f'SELECT e.* FROM journal_entries e WHERE {_ELIGIBLE}'
            ' AND e.context_prompt_day=? AND e.created_at<?'
            ' ORDER BY e.created_at DESC, e.id DESC', (today, end),
        ).fetchall()
