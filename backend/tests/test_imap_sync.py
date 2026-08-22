"""Tests for backend/email/sync.py's IMAP-based path, shared by the
'outlook' and 'imap' providers. Every imap_client/outlook_client call is
monkeypatched — no real network/sockets — matching test_email_sync.py's
style. run_bg is monkeypatched to run synchronously so classification side
effects are observable. Parametrized over both providers since they share
the same sync engine (backend/email/sync.py::_sync_imap) and differ only in
how connect() authenticates.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.email import imap_client, outlook_client, sync
from backend.email.sync import _store_parsed_message


@pytest.fixture(autouse=True)
def configured_oauth_clients(client):
    client.patch('/api/settings/ai', json={
        'microsoftOauthClientId': 'ms-cid', 'microsoftOauthClientSecret': 'ms-secret',
    })


@pytest.fixture(autouse=True)
def run_bg_sync(monkeypatch):
    """run_bg normally fires classify_email on a background thread — run it
    inline instead so tests can assert on its effects deterministically, and
    stub classify_email itself out (it's covered by test_email_ai.py)."""
    monkeypatch.setattr('backend.email.sync.run_bg', lambda fn: fn())
    classified = []
    monkeypatch.setattr('backend.ai.email.classify_email', lambda eid: classified.append(eid))
    return classified


def _parsed(uid, subject='Hi'):
    return {
        'providerMessageId': str(uid), 'threadId': None, 'subject': subject,
        'sender': 'Someone', 'senderEmail': f's{uid}@example.com', 'snippet': 'snip',
        'bodyText': 'body', 'bodyHtml': '', 'labelIds': [], 'receivedAt': 1700000000,
    }


def _raw(uid) -> bytes:
    return f'raw-{uid}'.encode()


def _make_account_row(db, provider: str) -> dict:
    now = int(time.time())
    account_id = str(ULID())
    if provider == 'outlook':
        db.execute(
            """
            INSERT INTO email_accounts
                (id, provider, email_address, access_token, refresh_token, token_expires_at,
                 sync_enabled, created_at, updated_at)
            VALUES (?, 'outlook', 'me@outlook.example', 'at', 'rt', ?, 1, ?, ?)
            """,
            (account_id, now + 3600, now, now),
        )
    else:
        db.execute(
            """
            INSERT INTO email_accounts
                (id, provider, email_address, imap_host, imap_port, imap_username, imap_password,
                 sync_enabled, created_at, updated_at)
            VALUES (?, 'imap', 'me@fastmail.example', 'imap.fastmail.example', 993,
                    'me@fastmail.example', 'app-pass', 1, ?, ?)
            """,
            (account_id, now, now),
        )
    db.commit()
    return dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_id,)).fetchone())


@pytest.fixture(params=['outlook', 'imap'])
def provider(request):
    return request.param


@pytest.fixture
def account_row(client, provider):
    return _make_account_row(get_db(), provider)


class _StubConn:
    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)

    def logout(self):
        self.logged_out = True


def _patch_connect(monkeypatch, conn, provider):
    if provider == 'outlook':
        monkeypatch.setattr(outlook_client, 'get_valid_access_token', lambda db, row, cid, cs: 'access-tok')
    monkeypatch.setattr(imap_client, 'connect', lambda *a, **k: conn)


def _patch_folder_status(monkeypatch, status: dict):
    monkeypatch.setattr(imap_client, 'folder_status', lambda conn: status)


def test_missing_outlook_oauth_client_is_an_error(client, monkeypatch):
    client.patch('/api/settings/ai', json={'microsoftOauthClientId': '', 'microsoftOauthClientSecret': ''})
    account_row = _make_account_row(get_db(), 'outlook')

    result = sync.sync_account(account_row)

    assert result['status'] == 'error'
    assert 'Microsoft' in result['error']
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['last_sync_error'] is None  # misconfiguration isn't an account-specific failure


def test_first_connect_backfills_and_sets_uid_cursor(client, monkeypatch, account_row, provider, run_bg_sync):
    conn = _StubConn()
    _patch_connect(monkeypatch, conn, provider)
    _patch_folder_status(monkeypatch, {'uidValidity': 55, 'uidNext': 3})
    monkeypatch.setattr(imap_client, 'search_all_uids', lambda c: [1, 2])
    monkeypatch.setattr(imap_client, 'fetch_message', lambda c, uid: _raw(uid))
    monkeypatch.setattr(imap_client, 'parse_message', lambda raw, uid: _parsed(uid))

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 2}
    rows = get_db().execute(
        'SELECT provider_message_id FROM emails WHERE account_id=?', (account_row['id'],)
    ).fetchall()
    assert {r['provider_message_id'] for r in rows} == {'1', '2'}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['uid_validity'] == 55
    assert updated['uid_next'] == 3
    assert updated['last_synced_at'] is not None
    assert updated['last_sync_error'] is None
    assert conn.logged_out is True
    assert sorted(run_bg_sync) == sorted(r['id'] for r in get_db().execute(
        'SELECT id FROM emails WHERE account_id=?', (account_row['id'],)
    ).fetchall())


def test_steady_state_incremental_uses_uid_search_since(client, monkeypatch, provider, run_bg_sync):
    db = get_db()
    account_row = _make_account_row(db, provider)
    db.execute('UPDATE email_accounts SET uid_validity=?, uid_next=? WHERE id=?', (55, 10, account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())

    conn = _StubConn()
    _patch_connect(monkeypatch, conn, provider)
    _patch_folder_status(monkeypatch, {'uidValidity': 55, 'uidNext': 12})
    captured_since = []

    def _search_since(c, uid_next):
        captured_since.append(uid_next)
        return [10, 11]

    monkeypatch.setattr(imap_client, 'search_uids_since', _search_since)
    monkeypatch.setattr(imap_client, 'fetch_message', lambda c, uid: _raw(uid))
    monkeypatch.setattr(imap_client, 'parse_message', lambda raw, uid: _parsed(uid))

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 2}
    assert captured_since == [10]
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['uid_next'] == 12


def test_uidvalidity_change_forces_full_rebackfill(client, monkeypatch, provider, run_bg_sync):
    """UIDVALIDITY changing is IMAP's equivalent of Gmail's history cursor
    expiring: every previously-remembered UID is meaningless, so the sync
    must re-list the whole mailbox rather than an incremental search against
    a UID space the server has invalidated."""
    db = get_db()
    account_row = _make_account_row(db, provider)
    db.execute('UPDATE email_accounts SET uid_validity=?, uid_next=? WHERE id=?', (1, 10, account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())

    conn = _StubConn()
    _patch_connect(monkeypatch, conn, provider)
    _patch_folder_status(monkeypatch, {'uidValidity': 2, 'uidNext': 5})  # changed since last sync
    monkeypatch.setattr(imap_client, 'search_all_uids', lambda c: [1, 2, 3])
    since_called = []
    monkeypatch.setattr(imap_client, 'search_uids_since', lambda c, n: since_called.append(1) or [])
    monkeypatch.setattr(imap_client, 'fetch_message', lambda c, uid: _raw(uid))
    monkeypatch.setattr(imap_client, 'parse_message', lambda raw, uid: _parsed(uid))

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 3}
    assert since_called == []
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['uid_validity'] == 2
    assert updated['uid_next'] == 5


def test_boundary_uid_replay_does_not_duplicate_or_reclassify(client, monkeypatch, provider, run_bg_sync):
    """search_uids_since already filters the RFC 3501 boundary UID (see
    test_imap_client.py); this pins the end-to-end behavior: even if a
    provider's SEARCH replayed an already-known UID, the existence check
    stops it from being inserted or classified twice."""
    db = get_db()
    account_row = _make_account_row(db, provider)
    db.execute('UPDATE email_accounts SET uid_validity=?, uid_next=? WHERE id=?', (55, 10, account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())
    _store_parsed_message(db, account_row['id'], '9', _parsed(9))
    run_bg_sync.clear()

    conn = _StubConn()
    _patch_connect(monkeypatch, conn, provider)
    _patch_folder_status(monkeypatch, {'uidValidity': 55, 'uidNext': 10})
    monkeypatch.setattr(imap_client, 'search_uids_since', lambda c, n: [9])
    monkeypatch.setattr(imap_client, 'fetch_message', lambda c, uid: _raw(uid))
    monkeypatch.setattr(imap_client, 'parse_message', lambda raw, uid: _parsed(uid))

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 0}
    assert run_bg_sync == []
    count = get_db().execute(
        'SELECT COUNT(*) c FROM emails WHERE account_id=?', (account_row['id'],)
    ).fetchone()['c']
    assert count == 1


def test_a_gone_message_is_skipped_not_fatal(client, monkeypatch, account_row, provider, run_bg_sync):
    conn = _StubConn()
    _patch_connect(monkeypatch, conn, provider)
    _patch_folder_status(monkeypatch, {'uidValidity': 1, 'uidNext': 3})
    monkeypatch.setattr(imap_client, 'search_all_uids', lambda c: [1, 2])

    def _fetch(c, uid):
        return None if uid == 1 else _raw(uid)  # deleted between SEARCH and FETCH

    monkeypatch.setattr(imap_client, 'fetch_message', _fetch)
    monkeypatch.setattr(imap_client, 'parse_message', lambda raw, uid: _parsed(uid))

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 1}


def test_connection_failure_is_recorded_and_never_raises(client, monkeypatch, account_row, provider):
    if provider == 'outlook':
        monkeypatch.setattr(outlook_client, 'get_valid_access_token', lambda *a: 'tok')

    def _boom(*a, **k):
        raise imap_client.ImapError('Could not connect to host:993')

    monkeypatch.setattr(imap_client, 'connect', _boom)

    result = sync.sync_account(account_row)

    assert result['status'] == 'error'
    assert 'Could not connect' in result['error']
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['last_sync_error'] == result['error']
