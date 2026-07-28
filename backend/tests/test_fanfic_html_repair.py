"""One-time repair of XenForo <noscript> image fallbacks stored as escaped text."""
import pytest

from backend.db import connection

_ESCAPED = (
    '<div><img alt="a.jpg" src="/api/fanfic/f1/images/a.jpg" title="">\n'
    '&lt;img alt="a.jpg" class="bbImage" data-zoom-target="1" height=""'
    ' src="/api/fanfic/f1/images/a.jpg" style="" title="" width=""/&gt;\n'
    '</div><p>Then she left.</p>'
)


def _use_db(path):
    prev = connection._DB_PATH, connection._conn
    if connection._conn is not None:
        connection._conn.close()
    connection._DB_PATH = str(path)
    connection._conn = None
    return prev


def _restore(prev):
    if connection._conn is not None:
        connection._conn.close()
    connection._DB_PATH, connection._conn = prev


@pytest.fixture
def unrepaired_db(tmp_path):
    """A DB holding a chapter imported by the pre-fix sanitizer.

    Built by running the real migrations, then dropping the marker column so
    the repair sees the DB as it would on the user's machine.
    """
    path = tmp_path / 'fanfic.db'
    prev = _use_db(path)
    try:
        connection.init_db()
        db = connection.get_db()
        db.execute(
            "INSERT INTO fics(id, title, source_type, created_at, updated_at)"
            " VALUES('f1', 'Fic', 'xenforo', 0, 0)"
        )
        db.execute(
            'INSERT INTO fic_chapters(id, fic_id, position, title, content_html,'
            ' content_text, word_count, created_at) VALUES(?,?,?,?,?,?,?,?)',
            ('c1', 'f1', 0, 'One', _ESCAPED, 'stale text', 99, 0),
        )
        db.execute('ALTER TABLE settings DROP COLUMN fic_escaped_img_repair')
        db.commit()
    finally:
        _restore(prev)
    return path


def test_repair_strips_fallback_and_refreshes_derived_columns(unrepaired_db):
    prev = _use_db(unrepaired_db)
    try:
        connection.init_db()
        row = connection.get_db().execute(
            'SELECT content_html, content_text, word_count FROM fic_chapters'
        ).fetchone()

        assert '&lt;img' not in row['content_html']
        assert 'bbImage' not in row['content_html']
        # The real image survives, and the prose around it is untouched.
        assert '<img alt="a.jpg" src="/api/fanfic/f1/images/a.jpg" title="">' in row['content_html']
        assert 'Then she left.' in row['content_html']
        # content_text / word_count are recomputed off the repaired HTML, so
        # the FTS index stops carrying the markup.
        assert 'bbImage' not in row['content_text']
        assert row['content_text'] != 'stale text'
        assert row['word_count'] == 3
    finally:
        _restore(prev)


def test_repair_runs_once(unrepaired_db):
    prev = _use_db(unrepaired_db)
    try:
        connection.init_db()
        db = connection.get_db()
        db.execute("UPDATE fic_chapters SET content_html='&lt;img src=\"x\"&gt;'")
        db.commit()
        _restore(prev)

        prev = _use_db(unrepaired_db)
        connection.init_db()
        # Marker column present, so the scan never reruns.
        row = connection.get_db().execute('SELECT content_html FROM fic_chapters').fetchone()
        assert row['content_html'] == '&lt;img src="x"&gt;'
    finally:
        _restore(prev)


def test_fts_search_no_longer_matches_stripped_markup(unrepaired_db):
    prev = _use_db(unrepaired_db)
    try:
        connection.init_db()
        db = connection.get_db()
        hits = db.execute(
            "SELECT id FROM fic_chapters_fts WHERE fic_chapters_fts MATCH 'bbImage'"
        ).fetchall()
        assert hits == []
    finally:
        _restore(prev)
