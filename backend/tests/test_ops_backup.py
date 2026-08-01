"""backend/ops/backup.py: the WAL-safe DB snapshot and rolling-retention pruning
that ops/backup.sh shells out to.
"""

import sqlite3
from datetime import date

import pytest

from backend.ops.backup import prune_candidates, snapshot_db


def test_snapshot_db_captures_wal_writes_not_yet_checkpointed(tmp_path):
    src = str(tmp_path / 'src.db')
    dest = str(tmp_path / 'snapshot.db')

    conn = sqlite3.connect(src)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)')
    conn.execute("INSERT INTO t (val) VALUES ('before')")
    conn.commit()

    # Second, still-open connection with an uncommitted write sitting in the WAL
    # file rather than the main db file — this is the case a plain `cp` of
    # src.db would silently miss.
    writer = sqlite3.connect(src)
    writer.execute("INSERT INTO t (val) VALUES ('in-wal')")
    writer.commit()

    try:
        snapshot_db(src, dest)
    finally:
        conn.close()
        writer.close()

    result = sqlite3.connect(dest).execute('SELECT val FROM t ORDER BY id').fetchall()
    assert [row[0] for row in result] == ['before', 'in-wal']


def test_snapshot_db_dest_must_not_already_exist(tmp_path):
    src = str(tmp_path / 'src.db')
    dest = str(tmp_path / 'snapshot.db')
    conn = sqlite3.connect(src)
    conn.execute('CREATE TABLE t (id INTEGER)')
    conn.commit()
    conn.close()
    with open(dest, 'w') as f:
        f.write('not a valid sqlite file')

    with pytest.raises(sqlite3.DatabaseError):
        snapshot_db(src, dest)


def test_prune_keeps_exactly_keep_days_window():
    today = date(2026, 8, 15)
    existing = ['2026-08-01', '2026-08-02', '2026-08-15']
    # keep_days=14 -> cutoff is 2026-08-02 (inclusive); 08-01 is one day too old.
    assert prune_candidates(existing, keep_days=14, today=today) == ['2026-08-01']


def test_prune_handles_gaps_without_disqualifying_neighbors():
    today = date(2026, 8, 15)
    existing = ['2026-08-01', '2026-08-14']  # missing days in between, e.g. drive unplugged
    assert prune_candidates(existing, keep_days=14, today=today) == ['2026-08-01']


def test_prune_ignores_malformed_entries():
    today = date(2026, 8, 15)
    existing = ['not-a-date', '2026-08-15', 'staging']
    assert prune_candidates(existing, keep_days=14, today=today) == []


def test_prune_empty_list():
    assert prune_candidates([], keep_days=14, today=date(2026, 8, 15)) == []


def test_prune_across_month_boundary():
    today = date(2026, 3, 3)
    existing = ['2026-02-15', '2026-02-17']
    # keep_days=14 -> cutoff is 2026-02-18; both fall before it.
    assert prune_candidates(existing, keep_days=14, today=today) == ['2026-02-15', '2026-02-17']
