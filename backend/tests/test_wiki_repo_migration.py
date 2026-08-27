"""Migrating a real, populated wiki to per-repo articles.

SQLite cannot drop a UNIQUE constraint, so giving `wiki_articles` a repo is a
create-copy-drop-rename over a table that already holds notes the user relies
on. These tests build a database at the *old* shape, run the migration, and
check that nothing was lost — including the two things a rebuild is most likely
to quietly break: the external-content FTS index keyed on rowid, and the three
triggers that keep it in sync, both of which DROP TABLE takes with it.
"""
import sqlite3
from pathlib import Path

import pytest

from backend.db import connection

_OLD_WIKI = """
CREATE TABLE wiki_articles (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    sources TEXT,
    tags TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    locked INTEGER NOT NULL DEFAULT 0,
    last_researched_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""

_ARTICLES = [
    ('id0', 'fsrs-spacing', 'FSRS spacing', 'how FSRS schedules',
     'A body about FSRS intervals.', '[{"url": "https://example.com"}]',
     '["learning"]', 3, 0, 900),
    ('id1', 'budget-apps', 'Budget apps', 'what other trackers do',
     'A body about budget tracking.', None, None, 1, 1, None),
]


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database at the pre-migration shape, with articles and revisions."""
    path = tmp_path / 'old.db'
    con = sqlite3.connect(path)
    con.executescript((Path('backend/db/schema.sql')).read_text())
    con.execute('DROP TABLE wiki_articles')
    con.executescript(_OLD_WIKI)
    for row in _ARTICLES:
        con.execute(
            'INSERT INTO wiki_articles(id, slug, title, summary, content, sources,'
            ' tags, revision, locked, last_researched_at, created_at, updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (*row, 1, 2))
    con.execute(
        'INSERT INTO wiki_revisions(id, article_id, revision, title, content,'
        ' diff, author, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        ('rev0', 'id0', 2, 'FSRS spacing', 'The older body', None, 'agent', 'edit', 1))
    con.commit()
    con.close()

    monkeypatch.setattr(connection, '_DB_PATH', str(path))
    monkeypatch.setattr(connection, '_conn', None)
    yield path
    if connection._conn is not None:
        connection._conn.close()


def test_every_article_survives_with_no_repo_and_the_research_kind(old_db):
    """These are web research notes about a problem space. Assigning them a
    repository would be inventing a fact; NULL is what "not about a codebase"
    already means everywhere else here."""
    connection.init_db()
    rows = {r['slug']: dict(r) for r in
            connection.get_db().execute('SELECT * FROM wiki_articles')}

    assert set(rows) == {'fsrs-spacing', 'budget-apps'}
    for row in rows.values():
        assert row['repo_id'] is None
        assert row['kind'] == 'research'


def test_every_column_carries_over_unchanged(old_db):
    connection.init_db()
    row = dict(connection.get_db().execute(
        "SELECT * FROM wiki_articles WHERE slug='fsrs-spacing'").fetchone())

    assert row['id'] == 'id0'
    assert row['title'] == 'FSRS spacing'
    assert row['content'] == 'A body about FSRS intervals.'
    assert row['sources'] == '[{"url": "https://example.com"}]'
    assert row['tags'] == '["learning"]'
    assert row['revision'] == 3
    assert row['last_researched_at'] == 900


def test_the_users_lock_survives(old_db):
    """A locked article is the user's veto over the agent. Losing it in a
    migration would let the agent quietly overwrite prose they had claimed."""
    connection.init_db()
    row = connection.get_db().execute(
        "SELECT locked FROM wiki_articles WHERE slug='budget-apps'").fetchone()
    assert row['locked'] == 1


def test_the_revision_history_is_still_attached(old_db):
    connection.init_db()
    rows = connection.get_db().execute(
        "SELECT * FROM wiki_revisions WHERE article_id='id0'").fetchall()
    assert len(rows) == 1
    assert rows[0]['content'] == 'The older body'


def test_full_text_search_survives_the_rowid_reassignment(old_db):
    """wiki_fts is external-content, keyed on wiki_articles.rowid — and a
    rebuild assigns new ones. This is why the migration runs *before*
    _init_wiki_fts rather than after it."""
    connection.init_db()
    hits = connection.search_wiki_fts('intervals')
    assert [h['id'] for h in hits] == ['id0']


def test_the_fts_triggers_are_back_after_the_table_was_dropped(old_db):
    """DROP TABLE takes its triggers with it. A write after the migration has
    to still reach the index, or search silently stops seeing new articles."""
    connection.init_db()
    from backend.research import wiki

    wiki.upsert_article('new-note', 'A brand new note', 'summary',
                        'text about parakeets', repo_id=None)
    assert [h['id'] for h in connection.search_wiki_fts('parakeets')]

    wiki.upsert_article('new-note', 'A brand new note', 'summary',
                        'text about elephants', repo_id=None)
    assert [h['id'] for h in connection.search_wiki_fts('elephants')]
    # The replaced body is gone from the index, not merely shadowed by it.
    assert not connection.search_wiki_fts('parakeets')


def test_running_it_twice_changes_nothing(old_db):
    """Every migration in this file is idempotent; a rebuild that ran again
    would drop the table it had just filled."""
    connection.init_db()
    connection.init_db()
    rows = connection.get_db().execute(
        'SELECT COUNT(*) c FROM wiki_articles').fetchone()
    assert rows['c'] == 2


def test_the_constraint_is_actually_the_new_one(old_db):
    connection.init_db()
    db = connection.get_db()
    db.execute(
        'INSERT INTO repos(id, slug, name, remote_url, branch, clone_state,'
        ' is_default, created_at, updated_at)'
        " VALUES ('r1','a','A','https://h/o/a.git','','ready',0,1,1)")
    db.commit()

    # The old global UNIQUE(slug) would have refused this.
    db.execute(
        'INSERT INTO wiki_articles(id, repo_id, slug, title, kind, created_at,'
        " updated_at) VALUES ('n1','r1','fsrs-spacing','FSRS here','code',1,1)")
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) c FROM wiki_articles WHERE slug='fsrs-spacing'"
    ).fetchone()['c'] == 2
