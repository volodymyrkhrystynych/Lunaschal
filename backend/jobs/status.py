"""Application status history and the single write path for stage changes."""
import time
from ulid import ULID


def record(db, application_id: str, status: str, *, source: str = 'manual',
           source_id: str | None = None, at: int | None = None) -> bool:
    row = db.execute('SELECT status FROM applications WHERE id=?',
                     (application_id,)).fetchone()
    if row is None or row['status'] == status:
        return False
    at = int(time.time()) if at is None else at
    db.execute('UPDATE applications SET status=?, updated_at=? WHERE id=?',
               (status, at, application_id))
    db.execute(
        'INSERT INTO application_status_events'
        ' (id, application_id, status, source, source_id, occurred_at)'
        ' VALUES (?, ?, ?, ?, ?, ?)',
        (str(ULID()), application_id, status, source, source_id, at),
    )
    return True


def seed(db, application_id: str, status: str = 'draft', *, at: int | None = None):
    at = int(time.time()) if at is None else at
    db.execute(
        'INSERT INTO application_status_events'
        ' (id, application_id, status, source, occurred_at) VALUES (?, ?, ?, ?, ?)',
        (str(ULID()), application_id, status, 'created', at),
    )
