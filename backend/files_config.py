"""Where the Files tab's cloud-drive root lives — resolved from the `settings`
table, mirroring `backend/ops/backup_config.py`'s shape.

Settings is the source of truth once set; `backend/routes/files.py`'s
`_files_root()` falls back to the `FILES_ROOT` env var (and then the historical
`~/notes` default) only when no `files_root` row value is present, so an
existing env-var-configured deployment keeps working unchanged until the user
picks a folder in Settings → Files.
"""

import sqlite3
from pathlib import Path


def get_config(db: sqlite3.Connection) -> dict:
    """The root the Files tab is currently pointed at, if Settings has one."""
    row = db.execute('SELECT files_root FROM settings LIMIT 1').fetchone()
    path = (row['files_root'] or '').strip() if row else ''
    return {
        'path': path,
        'source': 'settings' if path else 'unset',
    }


def set_config(db: sqlite3.Connection, *, path: str | None = None) -> dict:
    """Write the root. Returns the resulting config."""
    if path is not None:
        row = db.execute('SELECT id FROM settings LIMIT 1').fetchone()
        if row is None:
            raise ValueError('no settings row')
        from backend.db.connection import build_update

        build_update(db, 'settings', {'files_root': path.strip() or None}, 'id=?', (row['id'],))
        db.commit()
    return get_config(db)


def validate_root(path: str) -> str | None:
    """Why `path` cannot be the Files root, or None if it can.

    Unlike the backup destination, an unwritable or not-yet-existing folder is
    fine here — `files.py` already `mkdir(parents=True, exist_ok=True)`s the
    root on first use, so there is nothing to unplug-and-reconnect the way a
    backup drive can be.
    """
    path = path.strip()
    if not path:
        return 'Choose a folder for your files.'
    if not path.startswith('/'):
        return 'The files folder must be an absolute path.'
    p = Path(path)
    if p.exists() and not p.is_dir():
        return 'That path is a file, not a folder.'
    return None
