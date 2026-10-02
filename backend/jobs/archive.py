"""The Archive: everything in Jobs that no longer needs the user's attention.

Three kinds of row end up here, and only the first needs a column:

- **expired** — a posting nobody acted on within a week of it reaching the
  feed, or an application whose resume was ready for a week and never sent.
  `expire_stale` stamps `jobs.archived_at`. An application only counts as
  archived this way while it is still unsent (`draft`/`ready`): marking it
  submitted from the Archive is enough to bring it back, with nothing to
  clear.
- **dismissed** — the user said no from the feed. Dismissal stamps the same
  column, so the Archive can be ordered by when it happened; `updated_at`
  cannot serve because a board re-listing the posting bumps it nightly.
- **closed** — an application that was rejected, withdrawn or never answered.
  Membership is *derived from the status* rather than stamped, and that is
  what makes a ghosted application that finally gets a reply leave the
  Archive on its own: `linkage.advance_status` moves it to `acknowledged` or
  `interview`, and from then on it simply no longer matches.

Triage-rejected postings are deliberately not here. They were never in front
of the user, there are thousands of them, and they already have their own
audit list (`GET /filtered`) with its own Restore.

Nothing is deleted: the rows are kept whole, description included, because
"what kind of jobs were plentiful last spring" is a question this data can
answer later. The listing is bounded instead — `SEARCH_WINDOW_DAYS` back and
no further.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from backend.jobs.retention import CLOSED_STATUSES
from backend.recall import find_ci

DAY = 86400
EXPIRE_AFTER_DAYS = 7
SEARCH_WINDOW_DAYS = 182
DEFAULT_LIMIT = 200
REASONS = ('expired', 'dismissed', 'closed')

_CLOSED_SQL = '(' + ','.join(f"'{s}'" for s in sorted(CLOSED_STATUSES)) + ')'

# When an application's resume became ready: the newest 'ready' status event,
# falling back to updated_at for a row that somehow has none.
READY_AT_SQL = (
    "COALESCE((SELECT MAX(e.occurred_at) FROM application_status_events e"
    " WHERE e.application_id = a.id AND e.status = 'ready'), a.updated_at)"
)

# The one definition of "in the Archive", shared by the listing and by the
# pipeline's `archived` flag so the two can never disagree about a row.
ARCHIVED_SQL = (
    f'((a.id IS NULL AND (j.archived_at IS NOT NULL OR j.dismissed = 1))'
    f" OR (a.status IN ('draft','ready') AND j.archived_at IS NOT NULL)"
    f' OR a.status IN {_CLOSED_SQL})'
)


def _now() -> int:
    return int(time.time())


def expire_stale(db, *, days: int = EXPIRE_AFTER_DAYS, now: int | None = None) -> dict:
    """Archive what has sat untouched for `days`. Returns the counts.

    Measured from the later of when the posting arrived (or the resume became
    ready) and when the user last restored it — otherwise a restore would be
    undone on the very next tick.
    """
    now = _now() if now is None else now
    cutoff = now - days * DAY
    postings = db.execute(
        """
        UPDATE jobs SET archived_at = ?
        WHERE archived_at IS NULL AND dismissed = 0
          AND triage_state != 'rejected'
          AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)
          AND MAX(created_at, COALESCE(archive_restored_at, 0)) <= ?
        """,
        (now, cutoff),
    ).rowcount
    ready = db.execute(
        f"""
        UPDATE jobs SET archived_at = ?
        WHERE archived_at IS NULL AND id IN (
            SELECT j.id FROM jobs j JOIN applications a ON a.job_id = j.id
            WHERE a.status = 'ready' AND a.applied_at IS NULL
              AND MAX({READY_AT_SQL}, COALESCE(j.archive_restored_at, 0)) <= ?
        )
        """,
        (now, cutoff),
    ).rowcount
    db.commit()
    return {'postings': postings, 'ready': ready}


def set_dismissed(db, job_id: str, dismissed: bool, *, now: int | None = None) -> bool:
    """Dismiss or un-dismiss a posting, keeping the archive stamp in step.

    Un-dismissing counts as a restore, so the posting gets a fresh week rather
    than being swept straight back. Returns False for an unknown id.
    """
    now = _now() if now is None else now
    if dismissed:
        sql = ('UPDATE jobs SET archived_at=COALESCE(archived_at, ?), dismissed=1,'
               ' updated_at=? WHERE id=?')
    else:
        sql = ('UPDATE jobs SET archived_at=CASE WHEN dismissed=1 THEN NULL'
               ' ELSE archived_at END, archive_restored_at=CASE WHEN dismissed=1'
               ' THEN ? ELSE archive_restored_at END, dismissed=0, updated_at=?'
               ' WHERE id=?')
    cur = db.execute(sql, (now, now, job_id))
    db.commit()
    return cur.rowcount > 0


def restore(db, job_id: str, *, now: int | None = None) -> str:
    """Bring an expired or dismissed posting back for another week.

    Returns 'restored', 'not_found', 'not_archived', or 'closed'. A closed
    application is not restored here: its way back is a status change on the
    application, which is also what a reply email does by itself.
    """
    now = _now() if now is None else now
    row = db.execute(
        'SELECT j.archived_at, j.dismissed, a.status FROM jobs j'
        ' LEFT JOIN applications a ON a.job_id = j.id WHERE j.id=?',
        (job_id,),
    ).fetchone()
    if row is None:
        return 'not_found'
    if row['status'] in CLOSED_STATUSES:
        return 'closed'
    if row['archived_at'] is None and not row['dismissed']:
        return 'not_archived'
    db.execute(
        'UPDATE jobs SET archived_at=NULL, dismissed=0, archive_restored_at=?,'
        ' updated_at=? WHERE id=?',
        (now, now, job_id),
    )
    db.commit()
    return 'restored'


def matches(fields: list[str], query: str) -> bool:
    """Every word of the query appears somewhere in the fields, any case.

    Words rather than the whole phrase, so "toronto backend" finds a Backend
    Engineer posting in Toronto. Python rather than SQL `LIKE` for
    `recall.find_ci`'s reason: SQLite folds case for ASCII only.
    """
    words = query.split()
    return all(any(find_ci(f or '', w) is not None for f in fields) for w in words)


def _iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def list_archived(db, *, query: str = '', reason: str | None = None,
                  limit: int = DEFAULT_LIMIT, now: int | None = None) -> list[dict]:
    """The Archive, newest first, never reaching back past SEARCH_WINDOW_DAYS."""
    now = _now() if now is None else now
    since = now - SEARCH_WINDOW_DAYS * DAY
    rows = db.execute(
        f"""
        SELECT * FROM (
            SELECT j.id AS job_id, j.title, j.company, j.location, j.url,
                   j.description, j.triage_summary, j.posted_at, j.created_at,
                   a.id AS application_id, a.status, a.applied_at,
                   CASE WHEN a.status IN {_CLOSED_SQL} THEN 'closed'
                        WHEN a.id IS NULL AND j.dismissed = 1 THEN 'dismissed'
                        ELSE 'expired' END AS reason,
                   CASE WHEN a.status IN {_CLOSED_SQL}
                        THEN COALESCE(a.closed_at, a.updated_at)
                        ELSE COALESCE(j.archived_at, j.created_at) END AS moment
            FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
            WHERE {ARCHIVED_SQL}
        )
        WHERE moment >= ? AND (? IS NULL OR reason = ?)
        ORDER BY moment DESC
        """,
        (since, reason, reason),
    ).fetchall()

    query = (query or '').strip()
    out = []
    for r in rows:
        if query and not matches(
            [r['title'], r['company'], r['location'], r['description'],
             r['triage_summary']], query,
        ):
            continue
        out.append({
            'jobId': r['job_id'],
            'applicationId': r['application_id'],
            'title': r['title'],
            'company': r['company'],
            'location': r['location'],
            'url': r['url'],
            'summary': r['triage_summary'] or (r['description'] or '')[:300],
            'reason': r['reason'],
            'status': r['status'],
            'archivedAt': _iso(r['moment']),
            'postedAt': _iso(r['posted_at']),
            'seenAt': _iso(r['created_at']),
            'appliedAt': _iso(r['applied_at']),
        })
        if len(out) >= limit:
            break
    return out
