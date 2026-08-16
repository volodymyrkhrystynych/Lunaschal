"""Applying `linkage.py`'s judgement to the actual database.

This is the layer that reads `emails` rows the existing classifier already
tagged `category='job_application'` and hangs them off the right application.
It does no matching of its own — every decision lives in `linkage.py`, where it
can be tested — and it makes no model calls at all, which is why the scheduler
can afford to run it every few minutes instead of nightly.

`job_email_scans` records that an email was considered, so a mailbox with two
thousand messages is walked once rather than on every tick. The catch is that
"no match" is only true relative to the applications that existed at the time,
so `rescan_since` clears those verdicts whenever a new application appears.
"""
import logging
import time

from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.jobs import linkage, retention

logger = logging.getLogger(__name__)

# Bounded so one tick over a large backlog stays short.
SWEEP_BATCH = 200

# When a new application is submitted, mail from slightly before it is worth
# reconsidering: the confirmation often beats the user recording that they
# applied.
RESCAN_LOOKBACK_SECONDS = 14 * 86400


def open_applications(db) -> list[linkage.ApplicationFacts]:
    """Every submitted application, as the facts linkage scores against."""
    rows = db.execute(
        """
        SELECT a.id, a.applied_email, a.applied_at,
               j.company, j.title, j.url
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.applied_at IS NOT NULL
        """
    ).fetchall()
    return [
        linkage.ApplicationFacts(
            application_id=r['id'],
            company=r['company'] or '',
            title=r['title'] or '',
            job_url=r['url'] or '',
            applied_email=r['applied_email'] or '',
            applied_at=r['applied_at'],
        )
        for r in rows
    ]


def _email_facts(row) -> linkage.EmailFacts:
    return linkage.EmailFacts(
        sender_email=row['sender_email'] or '',
        subject=row['subject'] or '',
        body_text=row['body_text'] or '',
        received_at=row['received_at'] or 0,
    )


def unscanned_job_emails(db, limit: int = SWEEP_BATCH) -> list:
    """Classified job-application mail this module has not looked at yet."""
    return db.execute(
        """
        SELECT e.* FROM emails e
        LEFT JOIN job_email_scans s ON s.email_id = e.id
        WHERE e.category = 'job_application' AND s.email_id IS NULL
        ORDER BY e.received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def link(db, application_id: str, email_id: str, confidence: float,
         kind: str = 'auto', now: int | None = None) -> bool:
    """Record a link. False if it already existed."""
    now = int(time.time()) if now is None else now
    cursor = db.execute(
        'INSERT OR IGNORE INTO job_email_links'
        ' (id, application_id, email_id, link_kind, confidence, created_at)'
        ' VALUES (?, ?, ?, ?, ?, ?)',
        (str(ULID()), application_id, email_id, kind, confidence, now),
    )
    db.commit()
    return cursor.rowcount > 0


def apply_email_status(db, application_id: str, job_status: str | None,
                       now: int | None = None) -> str | None:
    """Advance the application's status from a linked email. Returns the new
    status, or None when nothing changed."""
    row = db.execute(
        'SELECT status FROM applications WHERE id=?', (application_id,)
    ).fetchone()
    if row is None:
        return None

    new_status = linkage.advance_status(row['status'], job_status)
    if new_status is None:
        return None

    now = int(time.time()) if now is None else now
    db.execute(
        'UPDATE applications SET status=?, updated_at=? WHERE id=?',
        (new_status, now, application_id),
    )
    db.commit()
    # Stamps closed_at and recomputes purge_after: a rejection is what starts
    # the shorter retention clock.
    retention.stamp_closed(db, application_id, new_status, now=now)
    return new_status


def scan_email(db, email_row, applications: list[linkage.ApplicationFacts],
               now: int | None = None) -> dict:
    """Consider one email. Links it when confident, records the scan either way."""
    now = int(time.time()) if now is None else now
    facts = _email_facts(email_row)
    top, confident = linkage.best_match(facts, applications)

    result = {
        'emailId': email_row['id'],
        'linked': False,
        'applicationId': None,
        'score': round(top.score, 3) if top else 0.0,
        'statusChange': None,
    }

    if top and confident:
        link(db, top.application_id, email_row['id'], top.score, 'auto', now=now)
        result['linked'] = True
        result['applicationId'] = top.application_id
        result['statusChange'] = apply_email_status(
            db, top.application_id, email_row['job_status'], now=now
        )

    db.execute(
        'INSERT OR REPLACE INTO job_email_scans (email_id, scanned_at, matched)'
        ' VALUES (?, ?, ?)',
        (email_row['id'], now, 1 if result['linked'] else 0),
    )
    db.commit()
    return result


def run_linkage_sweep(now: int | None = None, limit: int = SWEEP_BATCH) -> dict:
    """Scan every unconsidered job email. Cheap: string matching, no model."""
    now = int(time.time()) if now is None else now
    db = get_db()
    rows = unscanned_job_emails(db, limit)
    if not rows:
        return {'scanned': 0, 'linked': 0}

    applications = open_applications(db)
    linked = 0
    for row in rows:
        try:
            if scan_email(db, row, applications, now=now)['linked']:
                linked += 1
        except Exception as e:
            # One bad row must not stall the sweep forever; record the scan so
            # it isn't retried on every tick, and move on.
            logger.warning('Linkage failed for email %s: %s', row['id'], e)
            db.execute(
                'INSERT OR REPLACE INTO job_email_scans (email_id, scanned_at, matched)'
                ' VALUES (?, ?, 0)',
                (row['id'], now),
            )
            db.commit()

    return {'scanned': len(rows), 'linked': linked}


def rescan_since(db, since: int) -> int:
    """Forget the 'no match' verdicts for mail newer than `since`.

    Called when an application is submitted. Without it, every email that
    arrived before the application was recorded stays permanently unmatched,
    which is precisely the mail most likely to be its confirmation.
    """
    cursor = db.execute(
        'DELETE FROM job_email_scans WHERE matched = 0 AND email_id IN ('
        '  SELECT id FROM emails WHERE received_at >= ?'
        ')',
        (since - RESCAN_LOOKBACK_SECONDS,),
    )
    db.commit()
    return cursor.rowcount


def suggestions_for_email(db, email_id: str, limit: int = 5) -> list[dict]:
    """Plausible applications for an email that did not auto-link."""
    row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    if row is None:
        return []
    ranked = linkage.rank_candidates(_email_facts(row), open_applications(db))
    out = []
    for candidate in ranked[:limit]:
        if candidate.score < linkage.SUGGEST_THRESHOLD:
            continue
        app = db.execute(
            'SELECT a.id, j.company, j.title FROM applications a'
            ' JOIN jobs j ON j.id = a.job_id WHERE a.id=?',
            (candidate.application_id,),
        ).fetchone()
        if app is None:
            continue
        out.append({
            'applicationId': candidate.application_id,
            'company': app['company'],
            'title': app['title'],
            'score': round(candidate.score, 3),
            'reasons': candidate.reasons,
        })
    return out


def unlinked_job_emails(db, limit: int = 50) -> list[dict]:
    """Job mail that was scanned and matched nothing — the human queue."""
    rows = db.execute(
        """
        SELECT e.* FROM emails e
        JOIN job_email_scans s ON s.email_id = e.id AND s.matched = 0
        LEFT JOIN job_email_links l ON l.email_id = e.id
        WHERE e.category = 'job_application' AND l.id IS NULL
        ORDER BY e.received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]
