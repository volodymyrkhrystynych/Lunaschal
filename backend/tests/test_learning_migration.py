"""Migration of legacy SM-2 `flashcards` rows into `learning_cards`."""
import sqlite3
import time

import pytest

from backend.db import connection


@pytest.fixture
def legacy_db(tmp_path):
    """A DB file containing the pre-refactor flashcards table with rows."""
    path = tmp_path / 'legacy.db'
    db = sqlite3.connect(path)
    db.execute("""
        CREATE TABLE flashcards (
            id TEXT PRIMARY KEY,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            tags TEXT,
            source_id TEXT,
            easiness REAL DEFAULT 2.5,
            interval INTEGER DEFAULT 0,
            repetitions INTEGER DEFAULT 0,
            next_review INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    old_ts = 1600000000
    db.execute(
        "INSERT INTO flashcards VALUES ('c1', 'Q1', 'A1', '[\"py\"]', 'j1', 2.7, 21, 5, ?, ?)",
        (old_ts, old_ts),
    )
    db.execute(
        "INSERT INTO flashcards VALUES ('c2', 'Q2', 'A2', NULL, NULL, 2.5, 0, 0, ?, ?)",
        (old_ts, old_ts),
    )
    db.commit()
    db.close()
    return path


def _use_db(path):
    prev_path, prev_conn = connection._DB_PATH, connection._conn
    if prev_conn is not None:
        prev_conn.close()
    connection._DB_PATH = str(path)
    connection._conn = None
    return prev_path, prev_conn


def _restore(prev):
    if connection._conn is not None:
        connection._conn.close()
    connection._DB_PATH, connection._conn = prev


def test_migrates_rows_and_drops_table(legacy_db):
    prev = _use_db(legacy_db)
    try:
        before = int(time.time())
        connection.init_db()
        db = connection.get_db()

        rows = {r['id']: r for r in db.execute('SELECT * FROM learning_cards')}
        assert set(rows) == {'c1', 'c2'}
        c1, c2 = rows['c1'], rows['c2']
        assert (c1['question'], c1['answer']) == ('Q1', 'A1')
        assert c1['tags'] == '["py"]'
        assert c1['source_type'] == 'journal' and c1['source_id'] == 'j1'
        assert c2['source_type'] == 'manual' and c2['source_id'] is None
        # Scheduling reset: active, no FSRS state, due immediately.
        for row in (c1, c2):
            assert row['state'] == 'active'
            assert row['fsrs_state'] is None
            assert row['due'] >= before
            assert row['created_at'] == 1600000000

        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flashcards'"
        ).fetchone() is None
    finally:
        _restore(prev)


def test_rerun_is_noop(legacy_db):
    prev = _use_db(legacy_db)
    try:
        connection.init_db()
        connection.init_db()
        db = connection.get_db()
        count = db.execute('SELECT COUNT(*) FROM learning_cards').fetchone()[0]
        assert count == 2
    finally:
        _restore(prev)


def test_fresh_db_has_no_flashcards_table(tmp_path):
    prev = _use_db(tmp_path / 'fresh.db')
    try:
        connection.init_db()
        db = connection.get_db()
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flashcards'"
        ).fetchone() is None
        assert db.execute('SELECT COUNT(*) FROM learning_cards').fetchone()[0] == 0
    finally:
        _restore(prev)
