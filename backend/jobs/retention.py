"""Deleting tailored resumes once they have outlived their purpose.

Two triggers, both asked for: six months after applying, and shortly after a
rejection lands. Whichever comes first wins.

**Only the rendered files are deleted.** `resume_versions.content` — the
structured tailoring result, a few kilobytes — is kept forever, because the
question retention is meant to answer is "why am I still storing this PDF?",
not "what did I send these people?". A year later the second question is the
one that gets asked, usually right before a recruiter calls back.

The date arithmetic is pure and takes `now`, so the tests can move time instead
of waiting six months.
"""
import logging
import time

from backend.db.connection import build_update, get_db
from backend.jobs import storage

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 180
DEFAULT_REJECTION_GRACE_DAYS = 30

_DAY = 86400

# Statuses where the outcome is settled and the paperwork stops being useful.
# 'offer' is deliberately absent: that is the one where the file matters most.
CLOSED_STATUSES = frozenset({'rejected', 'withdrawn', 'ghosted'})


class RetentionPolicy:
    """Settings, defaulted. Constructed from a `settings` row."""

    def __init__(
        self,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        purge_on_rejection: bool = True,
        rejection_grace_days: int = DEFAULT_REJECTION_GRACE_DAYS,
    ):
        self.retention_days = retention_days
        self.purge_on_rejection = purge_on_rejection
        self.rejection_grace_days = rejection_grace_days

    @classmethod
    def from_settings(cls, row) -> 'RetentionPolicy':
        def _get(key, default):
            try:
                value = row[key]
            except (KeyError, IndexError, TypeError):
                return default
            return default if value is None else value

        if row is None:
            return cls()
        return cls(
            retention_days=int(_get('job_retention_days', DEFAULT_RETENTION_DAYS)),
            purge_on_rejection=bool(_get('job_purge_on_rejection', 1)),
            rejection_grace_days=int(
                _get('job_rejection_grace_days', DEFAULT_REJECTION_GRACE_DAYS)
            ),
        )


def purge_due_at(
    applied_at: int | None,
    status: str,
    closed_at: int | None,
    policy: RetentionPolicy,
) -> int | None:
    """When this application's rendered files become deletable.

    None means never on a schedule — an application that was never submitted
    keeps its draft renders until the application itself is deleted, because
    the clock this measures starts at "sent to someone else".
    """
    if not applied_at:
        return None

    due = applied_at + policy.retention_days * _DAY

    if policy.purge_on_rejection and status in CLOSED_STATUSES and closed_at:
        due = min(due, closed_at + policy.rejection_grace_days * _DAY)

    return due


def due_for_purge(
    applied_at: int | None,
    status: str,
    closed_at: int | None,
    policy: RetentionPolicy,
    now: int,
) -> bool:
    due = purge_due_at(applied_at, status, closed_at, policy)
    return due is not None and now >= due


def _load_policy(db) -> RetentionPolicy:
    row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
    return RetentionPolicy.from_settings(row)


def recompute_purge_after(db, application_id: str, status: str | None = None) -> int | None:
    """Refresh the denormalised `purge_after` for one application.

    Stored so the sweep can find work with an index instead of recomputing the
    policy across every row; recomputed on every status change because a
    rejection moves the date forward.

    `status` overrides the stored column. Callers know the status they are
    moving *to*, and depending on them to have written it first would make the
    purge date quietly wrong whenever they had not — a bug that surfaces six
    months later as a file that is still there, or one that isn't.
    """
    row = db.execute(
        'SELECT applied_at, status, closed_at FROM applications WHERE id=?',
        (application_id,),
    ).fetchone()
    if row is None:
        return None
    due = purge_due_at(
        row['applied_at'], status or row['status'], row['closed_at'], _load_policy(db)
    )
    db.execute('UPDATE applications SET purge_after=? WHERE id=?', (due, application_id))
    db.commit()
    return due


def purge_application(db, application_id: str, now: int | None = None) -> int:
    """Delete every rendered file for one application. Returns files removed.

    The DB is updated first and the filesystem second: a stamped row whose file
    is somehow still on disk is a leak the next sweep cleans up, while a
    deleted file whose row still promises a download is a 404 the user has to
    understand.
    """
    now = int(time.time()) if now is None else now
    versions = db.execute(
        'SELECT id, pdf_path, docx_path FROM resume_versions'
        ' WHERE application_id=? AND purged_at IS NULL',
        (application_id,),
    ).fetchall()

    paths = [p for v in versions for p in (v['pdf_path'], v['docx_path']) if p]

    db.execute(
        'UPDATE resume_versions SET purged_at=?, pdf_path=NULL, docx_path=NULL'
        ' WHERE application_id=? AND purged_at IS NULL',
        (now, application_id),
    )
    db.execute('UPDATE applications SET purged_at=? WHERE id=?', (now, application_id))
    db.commit()

    removed = 0
    for path_str in paths:
        path = storage.resolve_stored_path(path_str)
        if path is None:
            # Refuses anything that is no longer <root>/<app_id>/<file>.
            logger.warning('Refusing to purge path outside the jobs root: %s', path_str)
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as e:
            logger.warning('Could not delete %s: %s', path, e)

    # Take the directory too once it has nothing left in it.
    directory = storage.application_dir(application_id)
    if directory is not None and directory.is_dir() and not any(directory.iterdir()):
        storage.delete_application_dir(application_id)

    return removed


def run_purge_sweep(now: int | None = None) -> dict:
    """Purge every application whose retention date has passed."""
    now = int(time.time()) if now is None else now
    db = get_db()
    policy = _load_policy(db)

    rows = db.execute(
        'SELECT id, applied_at, status, closed_at FROM applications'
        ' WHERE purged_at IS NULL AND applied_at IS NOT NULL'
    ).fetchall()

    purged, files = 0, 0
    for row in rows:
        if not due_for_purge(row['applied_at'], row['status'], row['closed_at'], policy, now):
            continue
        files += purge_application(db, row['id'], now=now)
        purged += 1

    return {'applications': purged, 'files': files}


def stamp_closed(db, application_id: str, status: str, now: int | None = None) -> None:
    """Maintain `closed_at` as an application enters or leaves a closed state.

    It is what the rejection grace period is measured from, so it has to be the
    moment the outcome arrived — not `updated_at`, which editing a note months
    later would push forward and silently grant another month of storage.
    """
    now = int(time.time()) if now is None else now
    if status in CLOSED_STATUSES:
        row = db.execute(
            'SELECT closed_at FROM applications WHERE id=?', (application_id,)
        ).fetchone()
        # Don't restamp: 'rejected' -> 'ghosted' must not restart the clock.
        if row is not None and row['closed_at'] is None:
            db.execute('UPDATE applications SET closed_at=? WHERE id=?', (now, application_id))
    else:
        db.execute('UPDATE applications SET closed_at=NULL WHERE id=?', (application_id,))
    db.commit()
    recompute_purge_after(db, application_id, status)
