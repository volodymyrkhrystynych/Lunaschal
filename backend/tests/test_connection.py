"""Unit tests for backend.db.connection.build_update — the shared
UPDATE-clause builder used across the PATCH route handlers."""
from backend.db.connection import build_update, get_db


def test_sets_given_columns_and_appends_where_params(client):
    db = get_db()
    db.execute('CREATE TABLE t (id TEXT PRIMARY KEY, a TEXT, b INTEGER)')
    db.execute("INSERT INTO t (id, a, b) VALUES ('x', 'old', 1)")
    db.commit()

    build_update(db, 't', {'a': 'new', 'b': 2}, 'id=?', ('x',))
    db.commit()

    row = db.execute('SELECT a, b FROM t WHERE id=?', ('x',)).fetchone()
    assert row['a'] == 'new'
    assert row['b'] == 2


def test_supports_a_where_clause_with_no_params(client):
    db = get_db()
    db.execute('CREATE TABLE singleton (id INTEGER PRIMARY KEY, val TEXT)')
    db.execute("INSERT INTO singleton (id, val) VALUES (1, 'old')")
    db.commit()

    build_update(db, 'singleton', {'val': 'new'}, 'id=1')
    db.commit()

    row = db.execute('SELECT val FROM singleton WHERE id=1').fetchone()
    assert row['val'] == 'new'


def test_returns_the_cursor_so_callers_can_check_rowcount(client):
    db = get_db()
    db.execute('CREATE TABLE t2 (id TEXT PRIMARY KEY, a TEXT)')
    db.execute("INSERT INTO t2 (id, a) VALUES ('x', 'old')")
    db.commit()

    missed = build_update(db, 't2', {'a': 'new'}, 'id=?', ('missing',))
    db.commit()
    assert missed.rowcount == 0

    hit = build_update(db, 't2', {'a': 'new'}, 'id=?', ('x',))
    db.commit()
    assert hit.rowcount == 1
