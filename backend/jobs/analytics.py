"""Deterministic Jobs dashboard metrics."""
from collections import Counter
import time

from backend.jobs.keywords import BASE_TERMS, extract_terms

DAY = 86400
RESPONSE_STATUSES = ('acknowledged', 'interview', 'offer', 'rejected')


def skill_frequency(db, *, days: int = 30, limit: int = 12,
                    now: int | None = None) -> list[dict]:
    now = int(time.time()) if now is None else now
    rows = db.execute(
        'SELECT description FROM jobs WHERE created_at>=? AND description<>? ',
        (now - days * DAY, ''),
    ).fetchall()
    counts: Counter[str] = Counter()
    for row in rows:
        # Count postings, not mentions: ten repetitions in one JD should not
        # outweigh the same skill requested by ten employers.
        counts.update(extract_terms(row['description'], set(BASE_TERMS)).keys())
    return [
        {'term': term, 'postings': count, 'ofPostings': len(rows)}
        for term, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def funnel_metrics(db) -> dict:
    sent = db.execute(
        'SELECT COUNT(*) c FROM applications WHERE applied_at IS NOT NULL'
    ).fetchone()['c']
    responded = db.execute(
        "SELECT COUNT(DISTINCT application_id) c FROM application_status_events"
        " WHERE status IN ('acknowledged','interview','offer','rejected')"
    ).fetchone()['c']
    timing = db.execute(
        """SELECT AVG(first_response - applied_at) seconds
           FROM (
             SELECT a.id, a.applied_at, MIN(e.occurred_at) first_response
             FROM applications a JOIN application_status_events e
               ON e.application_id=a.id
             WHERE a.applied_at IS NOT NULL
               AND e.status IN ('acknowledged','interview','offer','rejected')
             GROUP BY a.id, a.applied_at
           )"""
    ).fetchone()['seconds']
    return {
        'sent': sent,
        'responded': responded,
        'responseRate': round(responded / sent, 3) if sent else 0,
        'averageResponseDays': round(timing / DAY, 1) if timing is not None else None,
    }


def weekly_activity(db, *, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    since = now - 7 * DAY
    scalar = lambda sql, params=(since,): db.execute(sql, params).fetchone()['c']
    return {
        'triaged': scalar('SELECT COUNT(*) c FROM jobs WHERE triage_at>=?'),
        'queued': scalar('SELECT COUNT(*) c FROM applications WHERE queued_at>=?'),
        'sent': scalar("SELECT COUNT(*) c FROM application_status_events WHERE status='submitted' AND occurred_at>=?"),
        'replies': scalar("SELECT COUNT(DISTINCT application_id) c FROM application_status_events WHERE status IN ('acknowledged','interview','offer','rejected') AND occurred_at>=?"),
    }


def source_conversion(db) -> list[dict]:
    rows = db.execute(
        """SELECT j.source,
                  COUNT(*) applications,
                  SUM(CASE WHEN a.applied_at IS NOT NULL THEN 1 ELSE 0 END) sent,
                  SUM(CASE WHEN EXISTS (
                    SELECT 1 FROM application_status_events e
                    WHERE e.application_id=a.id
                      AND e.status IN ('acknowledged','interview','offer','rejected')
                  ) THEN 1 ELSE 0 END) responded
           FROM applications a JOIN jobs j ON j.id=a.job_id
           GROUP BY j.source ORDER BY applications DESC, j.source"""
    ).fetchall()
    return [dict(row) | {
        'responseRate': round(row['responded'] / row['sent'], 3) if row['sent'] else 0
    } for row in rows]
