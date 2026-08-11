"""The blind-drill columns added to an existing practice_progress table
(_ensure_practice_recall_columns).

Worth pinning down because it runs against the user's real DB, which already
holds their typing history: the migration has to leave that history intact and
land the new columns in the exact state `backend/practice/modes.py` reads as
"never asked from memory". Getting those defaults wrong doesn't error — it
quietly mis-schedules every snippet in the bank.
"""
import sqlite3

from backend.db import connection
from backend.practice import modes

# practice_progress as it stood before blind drills existed.
OLD_SCHEMA = """
CREATE TABLE practice_progress (
    snippet_id TEXT PRIMARY KEY,
    attempts_count INTEGER NOT NULL DEFAULT 0,
    last_wpm REAL,
    last_accuracy REAL,
    best_wpm REAL,
    best_accuracy REAL,
    last_practiced_at INTEGER,
    updated_at INTEGER NOT NULL
);
"""


def _db(rows=()):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(OLD_SCHEMA)
    for snippet_id, attempts, wpm, accuracy in rows:
        db.execute(
            'INSERT INTO practice_progress(snippet_id, attempts_count, last_wpm,'
            ' last_accuracy, best_wpm, best_accuracy, last_practiced_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, 0, 0)',
            (snippet_id, attempts, wpm, accuracy, wpm, accuracy),
        )
    db.commit()
    return db


def _columns(db):
    return {r[1] for r in db.execute('PRAGMA table_info(practice_progress)')}


def test_adds_the_recall_columns():
    db = _db()
    connection._ensure_practice_recall_columns(db)
    assert {
        'recall_attempts_count', 'recall_passes', 'last_recall_passed', 'last_recall_at'
    } <= _columns(db)


def test_running_it_again_is_a_no_op():
    db = _db([('react-usestate', 3, 50.0, 100.0)])
    for _ in range(3):
        connection._ensure_practice_recall_columns(db)
    row = db.execute('SELECT * FROM practice_progress').fetchone()
    assert row['attempts_count'] == 3
    assert row['recall_attempts_count'] == 0


def test_an_existing_row_keeps_its_typing_history_and_unlocks_blind_on_it():
    """A snippet already typed out well does not have to earn the unlock again:
    the migration's defaults have to read as "never asked from memory", not as a
    failed recall (which would pin it to speed drills forever)."""
    db = _db([('react-usestate', 4, 50.0, 100.0)])
    connection._ensure_practice_recall_columns(db)

    row = dict(db.execute('SELECT * FROM practice_progress').fetchone())
    assert row['attempts_count'] == 4
    assert row['best_wpm'] == 50.0
    assert row['recall_attempts_count'] == 0
    assert row['recall_passes'] == 0
    assert row['last_recall_passed'] is None
    assert row['last_recall_at'] is None
    assert modes.next_mode(row) == modes.BLIND


def test_runs_on_startup(client):
    """Wired into init_db, not merely defined."""
    from backend.db.connection import get_db
    cols = {r[1] for r in get_db().execute('PRAGMA table_info(practice_progress)')}
    assert 'recall_attempts_count' in cols
