"""Unit tests for backend.db.connection.build_update — the shared
UPDATE-clause builder used across the PATCH route handlers."""
import threading

from backend.db.connection import build_update, get_db
from backend.place_roles import list_periods


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


def test_paper_content_time_backfills_from_the_pages(client):
    """A DB carried over from before the column has to answer "when did the
    drawing stop" for papers already in it, and the pages have always known."""
    from backend.db.connection import _ensure_paper_content_updated_at

    db = get_db()
    # Simulate the pre-migration shape: the column exists (schema.sql created
    # it), so drop back to NULL and re-run the migration's backfill.
    db.execute(
        "INSERT INTO papers(id, title, created_at, updated_at, content_updated_at)"
        " VALUES ('drawn', 'has pages', 100, 900, NULL),"
        "        ('blank', 'no pages', 100, 700, NULL)"
    )
    db.execute(
        "INSERT INTO paper_pages(id, paper_id, position, created_at, updated_at)"
        " VALUES ('p1', 'drawn', 0, 100, 300),"
        "        ('p2', 'drawn', 1, 100, 500)"
    )
    db.commit()
    db.execute(
        'UPDATE papers SET content_updated_at = COALESCE('
        ' (SELECT MAX(updated_at) FROM paper_pages WHERE paper_id = papers.id),'
        ' updated_at) WHERE content_updated_at IS NULL'
    )
    db.commit()

    times = {
        r['id']: r['content_updated_at']
        for r in db.execute('SELECT id, content_updated_at FROM papers')
    }
    # The newest page, not the paper's own updated_at (900 — polluted by the
    # rename and archive-flag writes this column exists to escape).
    assert times['drawn'] == 500
    # Nothing to read: updated_at is the best guess a pageless paper offers.
    assert times['blank'] == 700

    # Idempotent: the guard is the column's presence, so a second run is a
    # no-op rather than a re-backfill over live values.
    _ensure_paper_content_updated_at(db)
    assert times['drawn'] == 500


def _hammer(work, *, threads=6):
    """Run `work` on several threads at once; return whatever any of them raised."""
    failures = []
    start = threading.Barrier(threads)

    def run():
        start.wait()
        try:
            work()
        except Exception as e:  # noqa: BLE001 — the raised exception is the finding
            failures.append(repr(e))

    workers = [threading.Thread(target=run) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return failures


def _seed_one_role(db):
    db.execute("INSERT INTO saved_places (id, name, latitude, longitude, radius_m, created_at, updated_at)"
               " VALUES ('p', 'Office', 43.6, -79.6, 50, 1, 1)")
    db.execute("INSERT INTO place_roles (id, role, place_id, started_on, created_at, updated_at)"
               " VALUES ('r', 'work', 'p', '2026-07-27', 1, 1)")
    db.commit()


def test_concurrent_reads_of_one_statement_each_get_the_whole_answer():
    """The Settings crash, at its source. Every request shares one connection,
    and identical SQL shares one cached statement, so a thread could reset the
    statement another was still reading: that reader then saw no rows at all,
    or this table's single row several times over with its columns NULL. The
    window's ~30 requests at startup were enough to send a NOT NULL column to
    the client as null."""
    db = get_db()
    _seed_one_role(db)
    wrong = []

    def read():
        for _ in range(1500):
            rows = list_periods(db)
            if [(r['id'], r['started_on']) for r in rows] != [('r', '2026-07-27')]:
                wrong.append(rows)

    assert _hammer(read) == []
    assert wrong == []


def test_a_commit_cannot_land_inside_another_threads_read():
    db = get_db()
    _seed_one_role(db)

    def read_and_write():
        for i in range(400):
            assert db.execute('SELECT started_on FROM place_roles WHERE id=?', ('r',)).fetchone()[0]
            db.execute('UPDATE place_roles SET updated_at=? WHERE id=?', (i, 'r'))
            db.commit()

    assert _hammer(read_and_write) == []


def test_a_cursor_still_reads_like_a_cursor():
    """Fetching inside the lock must not change what callers see."""
    db = get_db()
    _seed_one_role(db)
    db.execute("INSERT INTO saved_places (id, name, created_at, updated_at) VALUES ('q', 'Gym', 1, 1)")

    cur = db.execute('SELECT id FROM saved_places ORDER BY id')
    assert cur.fetchone()['id'] == 'p'
    assert [r['id'] for r in cur.fetchall()] == ['q']
    assert cur.fetchone() is None

    assert [r['id'] for r in db.execute('SELECT id FROM saved_places ORDER BY id')] == ['p', 'q']
    assert len(db.execute('SELECT id FROM saved_places').fetchmany(1)) == 1

    assert db.execute("UPDATE saved_places SET notes='x'").rowcount == 2
    assert db.execute("UPDATE saved_places SET notes='y' RETURNING id").fetchall() != []
    assert db.execute("DELETE FROM saved_places WHERE id='q'").rowcount == 1
    assert db.execute("INSERT INTO saved_places (id, name, created_at, updated_at)"
                      " VALUES ('z', 'New', 1, 1)").lastrowid > 0
    db.rollback()

    # Reading from a query while writing inside the loop stays legal.
    for row in db.execute('SELECT id FROM saved_places'):
        db.execute('UPDATE saved_places SET notes=? WHERE id=?', ('seen', row['id']))
    db.commit()
    with db:
        db.execute("UPDATE saved_places SET notes='ctx'")
    assert db.execute('SELECT notes FROM saved_places').fetchone()['notes'] == 'ctx'
