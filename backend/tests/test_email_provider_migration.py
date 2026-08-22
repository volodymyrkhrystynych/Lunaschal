"""_ensure_provider_outlook_imap: the one migration in this file that's a
full table rebuild (SQLite has no ALTER TABLE for CHECK constraints) rather
than a guarded ADD COLUMN like every other migration in connection.py. Tested
in isolation, on a minimal schema, before anything in the app builds on it —
per the plan, this needs its own idempotency and cascade-delete coverage
since getting the PRAGMA foreign_keys toggle wrong would silently leave FK
enforcement off for the rest of the process on the real single-connection
app.
"""
import sqlite3

from backend.db import connection

_OLD_SCHEMA = """
CREATE TABLE email_accounts (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'gmail' CHECK(provider IN ('gmail')),
    email_address TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at INTEGER,
    scope TEXT,
    history_id TEXT,
    last_synced_at INTEGER,
    last_sync_error TEXT,
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(provider, email_address)
);
CREATE TABLE emails (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES email_accounts(id) ON DELETE CASCADE,
    provider_message_id TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(_OLD_SCHEMA)
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-1', 'gmail', 'me@example.com', 1, 100, 200)"
    )
    db.commit()
    return db


def _columns(db) -> set:
    return {r[1] for r in db.execute('PRAGMA table_info(email_accounts)')}


def test_widens_the_provider_check_and_adds_imap_columns():
    db = _db()
    connection._ensure_provider_outlook_imap(db)

    cols = _columns(db)
    for col in ('imap_host', 'imap_port', 'imap_username', 'imap_password', 'uid_validity', 'uid_next'):
        assert col in cols

    # The CHECK now accepts outlook and imap — proven by actually inserting,
    # not by string-matching the DDL.
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-2', 'outlook', 'me@outlook.example', 1, 100, 200)"
    )
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-3', 'imap', 'me@fastmail.example', 1, 100, 200)"
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) c FROM email_accounts WHERE provider IN ('outlook','imap')").fetchone()['c'] == 2


def test_rejects_an_unknown_provider():
    db = _db()
    connection._ensure_provider_outlook_imap(db)

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at)"
            " VALUES ('acct-x', 'yahoo', 'me@yahoo.example', 1, 100, 200)"
        )


def test_preserves_existing_row_data():
    db = _db()
    connection._ensure_provider_outlook_imap(db)

    row = db.execute("SELECT * FROM email_accounts WHERE id='acct-1'").fetchone()
    assert row['provider'] == 'gmail'
    assert row['email_address'] == 'me@example.com'
    assert row['created_at'] == 100
    assert row['updated_at'] == 200
    assert row['imap_host'] is None
    assert row['uid_validity'] is None


def test_is_idempotent():
    db = _db()
    connection._ensure_provider_outlook_imap(db)
    first_cols = _columns(db)
    first_row = dict(db.execute("SELECT * FROM email_accounts WHERE id='acct-1'").fetchone())

    for _ in range(3):
        connection._ensure_provider_outlook_imap(db)

    assert _columns(db) == first_cols
    assert dict(db.execute("SELECT * FROM email_accounts WHERE id='acct-1'").fetchone()) == first_row


def test_leaves_foreign_keys_enforcement_on():
    db = _db()
    connection._ensure_provider_outlook_imap(db)
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_cascade_delete_still_works_after_the_rebuild():
    """The whole reason the foreign_keys pragma toggle matters: emails.account_id
    ON DELETE CASCADE must survive the table rebuild, not just the column list."""
    db = _db()
    connection._ensure_provider_outlook_imap(db)

    db.execute(
        "INSERT INTO emails (id, account_id, provider_message_id, received_at, created_at)"
        " VALUES ('email-1', 'acct-1', 'm1', 100, 100)"
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) c FROM emails").fetchone()['c'] == 1

    db.execute("DELETE FROM email_accounts WHERE id='acct-1'")
    db.commit()

    assert db.execute("SELECT COUNT(*) c FROM emails").fetchone()['c'] == 0
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
