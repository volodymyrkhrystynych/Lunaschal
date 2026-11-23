"""FTS indexes are rebuilt at boot only when they can actually be stale.

An unconditional 'rebuild' in every `_init_*_fts` reindexed 2.6 GB of fic
chapters and 800 MB of email on every production start, ~75 s, which is what
pushed boots past the auto-deploy health check. The rebuild is still needed
when the index is new (backfill) or when a migration's table rebuild dropped
its triggers (rows written since are missing), and these tests pin both halves.
"""

import sqlite3

import pytest

from backend.db import connection
from backend.db.connection import (
    _init_emails_fts,
    _init_fanfic_fts,
    _init_fts,
    _init_messages_fts,
    _init_recipes_fts,
    _init_wiki_fts,
)

INITS = [_init_fts, _init_recipes_fts, _init_wiki_fts, _init_fanfic_fts,
         _init_emails_fts, _init_messages_fts]


@pytest.fixture
def raw():
    db = sqlite3.connect(connection._DB_PATH)
    db.execute('PRAGMA foreign_keys=OFF')
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    yield db, statements
    db.close()


def _rebuilds(statements):
    return [s for s in statements if "VALUES('rebuild')" in s]


def _add_message(db, mid, text):
    db.execute("INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES (?, 'c1', 'user', ?, 0)",
               (mid, text))


def _hits(db, term):
    return [r[0] for r in db.execute(
        'SELECT id FROM messages_fts WHERE messages_fts MATCH ?', (term,))]


def test_boot_with_everything_in_place_rebuilds_nothing(raw):
    db, statements = raw
    for init in INITS:
        init(db)
    assert _rebuilds(statements) == []


def test_missing_trigger_rebuilds_and_backfills_rows_written_meanwhile(raw):
    db, statements = raw
    # What a migration's DROP TABLE + RENAME does to the triggers.
    db.execute('DROP TRIGGER messages_ai')
    _add_message(db, 'm1', 'zanzibar')
    db.commit()
    assert _hits(db, 'zanzibar') == []

    _init_messages_fts(db)

    assert len(_rebuilds(statements)) == 1
    assert _hits(db, 'zanzibar') == ['m1']
    # And the trigger is back, so the next write is indexed without a rebuild.
    _add_message(db, 'm2', 'quokka')
    assert _hits(db, 'quokka') == ['m2']


def test_new_index_is_backfilled(raw):
    db, statements = raw
    _add_message(db, 'm1', 'zanzibar')
    db.commit()
    db.execute('DROP TABLE messages_fts')
    for name in ('messages_ai', 'messages_ad', 'messages_au'):
        db.execute(f'DROP TRIGGER {name}')

    _init_messages_fts(db)

    assert len(_rebuilds(statements)) == 1
    assert _hits(db, 'zanzibar') == ['m1']


def test_only_the_stale_index_is_rebuilt(raw):
    db, statements = raw
    db.execute('DROP TRIGGER emails_au')
    for init in INITS:
        init(db)
    assert [s for s in _rebuilds(statements)] == [
        "INSERT INTO emails_fts(emails_fts) VALUES('rebuild')"
    ]
