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


# --- retired RAG vector store ---

def test_drop_vector_tables_removes_the_retired_rag_store(client):
    """RAG is gone; a DB carried over from before must not keep its tables."""
    from backend.db.connection import _drop_vector_tables
    db = get_db()
    db.execute('CREATE TABLE IF NOT EXISTS embedding_metadata (id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS embeddings (id TEXT PRIMARY KEY)')
    db.commit()

    _drop_vector_tables(db)

    names = {r[0] for r in db.execute('SELECT name FROM sqlite_master')}
    assert 'embedding_metadata' not in names
    assert 'embeddings' not in names
    # Learning's own answer embeddings are a different thing entirely.
    assert db.execute('PRAGMA table_info(learning_cards)').fetchall()


def test_drop_vector_tables_is_idempotent(client):
    from backend.db.connection import _drop_vector_tables
    db = get_db()
    _drop_vector_tables(db)
    _drop_vector_tables(db)  # nothing left to find; must not raise


def test_fresh_schema_creates_no_embedding_tables(client):
    names = {r[0] for r in get_db().execute('SELECT name FROM sqlite_master')}
    assert not {n for n in names if 'embedding' in n and not n.startswith('learning')}


# --- retired chores list ---

def test_merge_chores_into_todos_folds_the_retired_list(client):
    """Chores were todos with list='chores'; the Lifestyle tab shows one merged
    list now, so a DB carried over from before must not keep rows on it."""
    from backend.db.connection import _merge_chores_into_todos
    db = get_db()
    db.execute(
        "INSERT INTO todos(id, title, done, list, priority, created_at, updated_at)"
        " VALUES ('c1', 'sweep', 0, 'chores', 3, 0, 0),"
        "        ('a1', 'set aside', 0, 'archive', 3, 0, 0)",
    )
    db.commit()

    _merge_chores_into_todos(db)

    lists = {r['id']: r['list'] for r in db.execute('SELECT id, list FROM todos')}
    assert lists == {'c1': 'todo', 'a1': 'archive'}  # archive is untouched

    _merge_chores_into_todos(db)  # idempotent: nothing left to match
    assert db.execute("SELECT COUNT(*) c FROM todos WHERE list='chores'").fetchone()['c'] == 0
