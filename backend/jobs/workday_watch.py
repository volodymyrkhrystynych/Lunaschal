"""Persisted polling for Workday CXS boards without widening jobs.source."""
import json
import time
from ulid import ULID
from backend.db.connection import row_to_dict
from backend.jobs import profile as profile_mod, sync
from backend.jobs.sources import workday


def create(db, url: str, label: str = '') -> dict:
    params = workday.parse_board_url(url)
    now = int(time.time()); board_id = str(ULID())
    db.execute('INSERT INTO workday_boards (id, url, label, params, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
               (board_id, url, label.strip(), json.dumps(params), now, now))
    db.commit(); run(db, board_id, now=now)
    return row_to_dict(db.execute('SELECT * FROM workday_boards WHERE id=?', (board_id,)).fetchone())


def run(db, board_id: str, *, now: int | None = None) -> dict:
    row = db.execute('SELECT * FROM workday_boards WHERE id=?', (board_id,)).fetchone()
    if row is None: raise LookupError(board_id)
    now = int(time.time()) if now is None else now
    try:
        fetched = workday.fetch(json.loads(row['params']))
        loaded = profile_mod.load_profile(db); added = updated = 0
        for job in fetched.jobs:
            exists = db.execute("SELECT 1 FROM jobs WHERE source='manual' AND source_id=?", (job['sourceId'],)).fetchone()
            if sync.upsert_job(db, 'manual', job, loaded):
                updated += bool(exists); added += not bool(exists)
        db.execute('UPDATE workday_boards SET last_run_at=?, last_count=?, last_error=NULL, updated_at=? WHERE id=?',
                   (now, len(fetched.jobs), now, board_id)); db.commit()
        return {'boardId': board_id, 'added': added, 'updated': updated, 'count': len(fetched.jobs)}
    except Exception as exc:
        db.execute('UPDATE workday_boards SET last_run_at=?, last_error=?, updated_at=? WHERE id=?',
                   (now, str(exc), now, board_id)); db.commit()
        return {'boardId': board_id, 'added': 0, 'updated': 0, 'count': 0, 'error': str(exc)}


def run_due(db, *, now: int | None = None) -> list[dict]:
    now = int(time.time()) if now is None else now
    rows = db.execute('SELECT * FROM workday_boards WHERE enabled=1').fetchall()
    return [run(db, row['id'], now=now) for row in rows
            if row['last_run_at'] is None or now-row['last_run_at'] >= row['interval_hours']*3600]
