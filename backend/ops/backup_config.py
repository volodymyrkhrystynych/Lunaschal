"""Where the nightly backup writes — resolved from the `settings` table.

Settings is the source of truth. `ops/backup.sh` asks this module for the
destination instead of reading `BACKUP_HDD_PATH` out of `ops/backup.env`, so
the path the UI shows and the path the job uses cannot drift apart. They did
drift once, and it cost nineteen days of backups.

`ops/backup.env` is still read for the tablet destination and as the seed for a
database that has never had a backup path set (see `_ensure_backup_settings`).
It is a fallback, not an authority: once the DB holds a path, the file's
`BACKUP_HDD_PATH` is ignored.

The path-shape helpers are pure and clock-free so the picker's validation and
the status panel's classification agree without either touching the other.
"""

import os
import sqlite3
from pathlib import Path

DEFAULT_RETENTION_DAYS = 14

# Retention is a rolling window of dated DB snapshots. One is a floor rather
# than zero because a "backup" that keeps nothing is a deletion schedule; the
# ceiling only stops a typo turning into a drive full of snapshots.
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365

_ENV_PATH = Path(__file__).resolve().parents[2] / 'ops' / 'backup.env'


def env_hdd_path(env_path: Path | None = None) -> str:
    """`BACKUP_HDD_PATH` from ops/backup.env, or '' if unreadable/unset."""
    from backend.ops.backup_status import parse_env

    try:
        return parse_env((env_path or _ENV_PATH).read_text()).get('BACKUP_HDD_PATH', '').strip()
    except OSError:
        return ''


def seed_from_env_file(db: sqlite3.Connection, env_path: Path | None = None) -> str:
    """Copy the file's destination into a settings row that has none.

    Called once, from the migration that adds the column. Returns what was
    seeded (empty string if there was nothing to seed) so the migration is
    testable without inspecting the DB afterwards.
    """
    path = env_hdd_path(env_path)
    if not path:
        return ''
    row = db.execute('SELECT id, backup_path FROM settings LIMIT 1').fetchone()
    if row is None:
        return ''
    # Never clobber a value that is somehow already there — a re-run of the
    # migration must not undo a change the user made in between.
    if row['backup_path']:
        return ''
    db.execute('UPDATE settings SET backup_path=? WHERE id=?', (path, row['id']))
    return path


def get_config(db: sqlite3.Connection) -> dict:
    """The destination and retention the nightly job will actually use."""
    row = db.execute(
        'SELECT backup_path, backup_retention_days FROM settings LIMIT 1'
    ).fetchone()

    path = (row['backup_path'] or '').strip() if row else ''
    source = 'settings'
    if not path:
        # A DB with no path at all (a fresh install whose migration found no
        # env file) still defers to the file if one appears later.
        path = env_hdd_path()
        source = 'backup.env' if path else 'unset'

    retention = row['backup_retention_days'] if row else None
    return {
        'path': path,
        'retentionDays': clamp_retention(retention),
        'source': source,
    }


def set_config(
    db: sqlite3.Connection,
    *,
    path: str | None = None,
    retention_days: int | None = None,
) -> dict:
    """Write the destination and/or retention. Returns the resulting config."""
    updates: dict = {}
    if path is not None:
        updates['backup_path'] = path.strip() or None
    if retention_days is not None:
        updates['backup_retention_days'] = clamp_retention(retention_days)

    if updates:
        row = db.execute('SELECT id FROM settings LIMIT 1').fetchone()
        if row is None:
            raise ValueError('no settings row')
        from backend.db.connection import build_update

        build_update(db, 'settings', updates, 'id=?', (row['id'],))
        db.commit()
    return get_config(db)


def clamp_retention(value) -> int:
    """Coerce anything to a usable retention count.

    Anything unparseable becomes the default rather than raising: this is read
    on the backup's own code path, and a junk value in one column is not a
    reason to skip the night's backup entirely.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, n))


def validate_destination(path: str) -> str | None:
    """Why `path` cannot be a backup destination, or None if it can.

    Checks only what is wrong *independently of the drive being plugged in* —
    an unplugged destination is a valid setting, and rejecting it would make the
    panel unconfigurable exactly when you most want to fix it. Whether the
    drive is currently reachable is the status endpoint's job, not this one's.
    """
    path = path.strip()
    if not path:
        return 'Choose a folder for the backup.'
    if not path.startswith('/'):
        return 'The backup destination must be an absolute path.'

    p = Path(path)
    if p.exists() and not p.is_dir():
        return 'That path is a file, not a folder.'

    # Backing up into the data directory being backed up would copy the mirror
    # into itself on every run until the disk filled.
    data_root = Path(os.environ.get('LUNASCHAL_DATA_ROOT', 'data')).resolve()
    try:
        if p.resolve().is_relative_to(data_root):
            return 'The backup cannot live inside the data directory it backs up.'
    except (OSError, ValueError):
        pass
    return None


def nearest_mount_point(path: str) -> str | None:
    """The mount point the destination sits on, for display.

    Walks up to the first existing ancestor that is a mount point. Returns None
    when nothing along the path exists — which is itself the answer the status
    panel wants, since it means the drive is not there.
    """
    p = Path(path)
    while True:
        try:
            if p.exists() and os.path.ismount(p):
                return str(p)
        except OSError:
            return None
        if p.parent == p:
            return None
        p = p.parent


def destination_present(*, dest_exists: bool, parent_exists: bool,
                        parent_is_mount: bool) -> bool:
    """Whether the drive is there, given three facts about the path.

    Pure so both `ops/backup.sh` (via the CLI) and the status endpoint can agree.

    The destination existing is the primary signal, because the folder picker
    only ever offers folders that already exist — so a chosen destination that
    has since vanished means the drive is gone. The parent clause covers the one
    case where the destination legitimately does not exist yet: a path typed
    into `backup.env` before the first run, whose parent is the drive's mount
    point. Requiring the parent to be an actual mount point is what keeps a
    typo'd path under `/home` from reading as a mounted drive.
    """
    return dest_exists or (parent_exists and parent_is_mount)
