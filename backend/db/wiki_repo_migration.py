"""Rebuilds of `wiki_articles` that SQLite cannot express as an ALTER.

Two of them now, both create-copy-drop-rename and both for the same reason:
SQLite can add a column but cannot change a constraint. They live together
because the recipe is delicate — foreign keys off outside the transaction, an
explicit BEGIN, a `PRAGMA foreign_key_check` before COMMIT — and a second copy
of it written from memory is how the revision history gets dropped on the floor.

---

`ensure_wiki_repo_scope`: give wiki articles a repository.

The wiki was written for one thing — web research about the problem space an
idea lives in — and its `slug` is `UNIQUE` across the whole table. Once there
are several repositories, each with its own code wiki, two of them will both
want an article called `scheduling`, and one of them will silently overwrite the
other's.

SQLite cannot drop a constraint, so this is a create-copy-drop-rename. There is
precedent in this file's neighbour: `_ensure_provider_outlook_imap` rebuilds
`email_accounts` the same way, down to the `PRAGMA foreign_key_check` before
committing.

**Ordering matters twice.**

`wiki_fts` is an external-content FTS5 table keyed on `wiki_articles.rowid`, and
a rebuild assigns new rowids. `DROP TABLE` also drops the three triggers that
keep it in sync. Both are fine — *provided* this runs before `_init_wiki_fts`,
which recreates the triggers and issues a `'rebuild'`. Called after it, the
index would point at rowids that no longer mean anything.

And every existing row migrates to `repo_id = NULL, kind = 'research'`. Those
articles are web research notes that belong to no repository; giving them one
would be inventing a fact, and NULL is what "this note is not about a codebase"
already means everywhere else here.
"""
import sqlite3


def ensure_wiki_repo_scope(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(wiki_articles)')}
    if not cols:
        return
    if 'repo_id' in cols:
        _ensure_indexes(db)
        return

    # **Foreign keys off for the duration, and this is not optional.**
    # wiki_revisions references wiki_articles ON DELETE CASCADE, so with
    # enforcement on, `DROP TABLE wiki_articles` deletes every revision row —
    # the entire audit trail that makes an agent editing the user's prose
    # acceptable in the first place. Caught by a test that populated a
    # revision and looked for it afterwards.
    #
    # The pragma is a no-op inside a transaction, so it has to be set before
    # BEGIN and restored after COMMIT — the order SQLite's own
    # "making other kinds of table schema changes" recipe prescribes.
    db.execute('PRAGMA foreign_keys=OFF')
    # An explicit transaction around the whole rebuild: a crash between the
    # copy and the rename would otherwise leave two half-tables and no wiki.
    db.execute('BEGIN')
    try:
        db.execute("""
            CREATE TABLE wiki_articles_new (
                id TEXT PRIMARY KEY,
                repo_id TEXT REFERENCES repos(id) ON DELETE CASCADE,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                sources TEXT,
                tags TEXT,
                -- 'research' is a note about a problem space, written from the
                -- web and belonging to no repo. 'code' is a note about one
                -- module of one repository, written by reading it.
                kind TEXT NOT NULL DEFAULT 'research'
                    CHECK(kind IN ('research','code')),
                revision INTEGER NOT NULL DEFAULT 1,
                locked INTEGER NOT NULL DEFAULT 0,
                last_researched_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                -- The whole point of the rebuild: two repos may each have a
                -- `scheduling` article. SQLite treats NULLs as distinct here,
                -- which is what keeps the old global notes from colliding.
                UNIQUE(repo_id, slug)
            )
        """)
        db.execute("""
            INSERT INTO wiki_articles_new
                (id, repo_id, slug, title, summary, content, sources, tags, kind,
                 revision, locked, last_researched_at, created_at, updated_at)
            SELECT id, NULL, slug, title, summary, content, sources, tags,
                   'research', revision, locked, last_researched_at,
                   created_at, updated_at
            FROM wiki_articles
        """)
        db.execute('DROP TABLE wiki_articles')
        db.execute('ALTER TABLE wiki_articles_new RENAME TO wiki_articles')
        _ensure_indexes(db)
        problems = db.execute('PRAGMA foreign_key_check').fetchall()
        if problems:
            raise RuntimeError(f'wiki_articles migration broke FK integrity: {problems}')
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
    finally:
        # Restored whichever way the rebuild went: leaving enforcement off
        # would silently disable it for every write the process makes after.
        db.execute('PRAGMA foreign_keys=ON')


def ensure_wiki_life_kind(db: sqlite3.Connection) -> None:
    """Widen `kind` to allow 'life' — notes about the user, not about code.

    Same create-copy-drop-rename as `ensure_wiki_repo_scope` above, same
    ordering constraint: it must run **before** `_init_wiki_fts`, because the
    rebuild assigns new rowids and `DROP TABLE` takes the FTS triggers with it.

    Detected by reading the stored CHECK rather than by looking for a column,
    since nothing about the table's shape changes — only what the constraint
    will accept. A database created after this landed already has 'life' in its
    schema.sql and skips straight past.
    """
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiki_articles'"
    ).fetchone()
    if row is None or not row[0]:
        return
    if "'life'" in row[0]:
        return

    db.execute('PRAGMA foreign_keys=OFF')
    db.execute('BEGIN')
    try:
        db.execute("""
            CREATE TABLE wiki_articles_new (
                id TEXT PRIMARY KEY,
                repo_id TEXT REFERENCES repos(id) ON DELETE CASCADE,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                sources TEXT,
                tags TEXT,
                -- 'research' is a note about a problem space, written from the
                -- web and belonging to no repo. 'code' is a note about one
                -- module of one repository, written by reading it. 'life' is a
                -- note about the user, written by the nightly pass from their
                -- own journal, calendar, food, workouts and chats.
                kind TEXT NOT NULL DEFAULT 'research'
                    CHECK(kind IN ('research','code','life')),
                revision INTEGER NOT NULL DEFAULT 1,
                locked INTEGER NOT NULL DEFAULT 0,
                last_researched_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(repo_id, slug)
            )
        """)
        db.execute("""
            INSERT INTO wiki_articles_new
                (id, repo_id, slug, title, summary, content, sources, tags, kind,
                 revision, locked, last_researched_at, created_at, updated_at)
            SELECT id, repo_id, slug, title, summary, content, sources, tags,
                   kind, revision, locked, last_researched_at,
                   created_at, updated_at
            FROM wiki_articles
        """)
        db.execute('DROP TABLE wiki_articles')
        db.execute('ALTER TABLE wiki_articles_new RENAME TO wiki_articles')
        _ensure_indexes(db)
        problems = db.execute('PRAGMA foreign_key_check').fetchall()
        if problems:
            raise RuntimeError(f'wiki_articles life-kind migration broke FK integrity: {problems}')
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def _ensure_indexes(db: sqlite3.Connection) -> None:
    """The indexes for the post-migration shape.

    Not in schema.sql, because that file runs before every migration and one of
    these names a column an unmigrated table does not have yet.
    """
    db.execute(
        'CREATE INDEX IF NOT EXISTS idx_wiki_articles_updated'
        ' ON wiki_articles(updated_at DESC)'
    )
    db.execute(
        'CREATE INDEX IF NOT EXISTS idx_wiki_articles_repo'
        ' ON wiki_articles(repo_id, kind, updated_at DESC)'
    )


def ensure_snapshot_repo(db: sqlite3.Connection) -> None:
    """Which repo a snapshot describes. A plain guarded ALTER — repo_snapshots
    has no constraint that needs rewriting, only a new column."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(repo_snapshots)')}
    if 'repo_id' not in cols:
        db.execute(
            'ALTER TABLE repo_snapshots ADD COLUMN repo_id TEXT REFERENCES repos(id)'
        )
        db.execute(
            'CREATE INDEX IF NOT EXISTS idx_repo_snapshots_repo'
            ' ON repo_snapshots(repo_id, generated_at DESC, id DESC)'
        )
        db.commit()
