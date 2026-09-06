"""Which completed torrents have aged out.

Pure, so the boundary conditions are testable without a client or a clock —
the same reasoning as backend/ops/backup_status.py.

Retention is **opt-in per torrent**, and off unless the row says otherwise.
Deleting someone's downloads on a timer is not a reasonable default, and the
failure mode is unrecoverable: an aged-out torrent is gone from disk *and*
usually gone from the swarm's reach, so a wrong guess here cannot be undone by
re-running anything.
"""

DAY = 86400


def is_due(row: dict, now: int) -> bool:
    """A row is due when it has a positive retention, has actually finished, and
    that many days have passed since it finished.

    Deliberately keyed on `completed_at` rather than `added_at`: a torrent that
    took a week to download has not been *kept* for a week.
    """
    days = row.get('retention_days') or 0
    if days <= 0:
        return False
    completed = row.get('completed_at')
    if not completed:
        return False
    return now - completed >= days * DAY


def torrents_to_purge(rows: list[dict], now: int) -> list[dict]:
    return [r for r in rows if is_due(r, now)]
