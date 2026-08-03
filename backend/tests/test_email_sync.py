"""Tests for backend/email/sync.py::sync_account. Every gmail_client HTTP
call is monkeypatched — no real network — matching test_newspapers.py's
style. run_bg is monkeypatched to run synchronously so classification side
effects (stubbed out here) are observable in the same test."""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.email import gmail_client, sync


@pytest.fixture(autouse=True)
def configured_oauth_client(client):
    client.patch('/api/settings/ai', json={
        'googleOauthClientId': 'cid', 'googleOauthClientSecret': 'csecret',
    })


@pytest.fixture(autouse=True)
def run_bg_sync(monkeypatch):
    """run_bg normally fires classify_email on a background thread — run it
    inline instead so tests can assert on its effects deterministically, and
    stub classify_email itself out (it's covered by test_email_ai.py)."""
    monkeypatch.setattr('backend.email.sync.run_bg', lambda fn: fn())
    classified = []
    monkeypatch.setattr(
        'backend.ai.email.classify_email', lambda eid: classified.append(eid)
    )
    return classified


@pytest.fixture
def account_row(client):
    db = get_db()
    now = int(time.time())
    account_id = str(ULID())
    db.execute(
        """
        INSERT INTO email_accounts
            (id, provider, email_address, access_token, refresh_token, token_expires_at,
             scope, history_id, sync_enabled, created_at, updated_at)
        VALUES (?, 'gmail', 'me@example.com', 'at', 'rt', ?, 'scope', NULL, 1, ?, ?)
        """,
        (account_id, now + 3600, now, now),
    )
    db.commit()
    return dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_id,)).fetchone())


def _gmail_message(gmail_id, subject='Hi', sender='a@b.com'):
    import base64
    return {
        'id': gmail_id, 'threadId': f'thread-{gmail_id}', 'labelIds': ['INBOX'], 'snippet': 'snip',
        'internalDate': '1700000000000',
        'payload': {
            'headers': [{'name': 'Subject', 'value': subject}, {'name': 'From', 'value': sender}],
            'mimeType': 'text/plain',
            'body': {'data': base64.urlsafe_b64encode(b'body text').decode()},
        },
    }


def test_missing_oauth_client_is_an_error(client, account_row):
    client.patch('/api/settings/ai', json={'googleOauthClientId': '', 'googleOauthClientSecret': ''})
    result = sync.sync_account(account_row)
    assert result['status'] == 'error'


def test_first_connect_backfills_and_sets_history_id(client, monkeypatch, account_row, run_bg_sync):
    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')
    monkeypatch.setattr(
        gmail_client, 'list_message_ids_since',
        lambda token, after_date, page_token=None: {'messages': [{'id': 'm1'}, {'id': 'm2'}]},
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': 'me@example.com', 'historyId': 'h100'})

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 2}
    rows = get_db().execute('SELECT gmail_id FROM emails WHERE account_id=?', (account_row['id'],)).fetchall()
    assert {r['gmail_id'] for r in rows} == {'m1', 'm2'}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['history_id'] == 'h100'
    assert updated['last_synced_at'] is not None
    assert updated['last_sync_error'] is None
    assert sorted(run_bg_sync) == sorted(r['id'] for r in get_db().execute(
        'SELECT id FROM emails WHERE account_id=?', (account_row['id'],)
    ).fetchall())


def test_rerunning_backfill_is_idempotent(client, monkeypatch, account_row):
    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')
    monkeypatch.setattr(
        gmail_client, 'list_message_ids_since',
        lambda token, after_date, page_token=None: {'messages': [{'id': 'm1'}]},
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': 'me@example.com', 'historyId': 'h100'})

    first = sync.sync_account(account_row)
    assert first['newCount'] == 1

    # history_id is now set, so a second call goes through the incremental
    # path, not backfill again — simulate an empty history delta.
    monkeypatch.setattr(
        gmail_client, 'list_history',
        lambda token, start_history_id, page_token=None: {'history': [], 'historyId': start_history_id},
    )
    updated_row = dict(get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())
    second = sync.sync_account(updated_row)
    assert second == {'status': 'ok', 'newCount': 0}
    count = get_db().execute('SELECT COUNT(*) c FROM emails WHERE account_id=?', (account_row['id'],)).fetchone()['c']
    assert count == 1


def test_incremental_sync_inserts_new_messages(client, monkeypatch, account_row):
    db = get_db()
    db.execute('UPDATE email_accounts SET history_id=? WHERE id=?', ('h1', account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())

    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')
    monkeypatch.setattr(
        gmail_client, 'list_history',
        lambda token, start_history_id, page_token=None: {
            'history': [{'messagesAdded': [{'message': {'id': 'm9'}}]}],
            'historyId': 'h2',
        },
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 1}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['history_id'] == 'h2'


def test_expired_history_cursor_falls_back_to_backfill(client, monkeypatch, account_row):
    db = get_db()
    db.execute('UPDATE email_accounts SET history_id=? WHERE id=?', ('stale', account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())

    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')

    def raise_expired(token, start_history_id, page_token=None):
        raise gmail_client.HistoryExpiredError('expired')

    monkeypatch.setattr(gmail_client, 'list_history', raise_expired)
    monkeypatch.setattr(
        gmail_client, 'list_message_ids_since',
        lambda token, after_date, page_token=None: {'messages': [{'id': 'recovered1'}]},
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': 'me@example.com', 'historyId': 'h-fresh'})

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 1}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['history_id'] == 'h-fresh'
    row = get_db().execute('SELECT gmail_id FROM emails WHERE account_id=?', (account_row['id'],)).fetchone()
    assert row['gmail_id'] == 'recovered1'


def test_backfill_snapshots_history_id_before_paging_messages(client, monkeypatch, account_row):
    """Regression: get_profile (the history_id baseline) must be called
    before list_message_ids_since starts paginating, not after. A message
    arriving mid-backfill would already be missed by pagination (newest
    first) but, if the baseline were snapshotted afterwards, would also fall
    at-or-before that baseline and so be silently skipped by every future
    incremental sync too."""
    call_order = []
    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')

    def fake_get_profile(token):
        call_order.append('get_profile')
        return {'emailAddress': 'me@example.com', 'historyId': 'h100'}

    def fake_list_message_ids_since(token, after_date, page_token=None):
        call_order.append('list_message_ids_since')
        return {'messages': [{'id': 'm1'}]}

    monkeypatch.setattr(gmail_client, 'get_profile', fake_get_profile)
    monkeypatch.setattr(gmail_client, 'list_message_ids_since', fake_list_message_ids_since)
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))

    sync.sync_account(account_row)

    assert call_order[0] == 'get_profile'


def test_history_recovery_window_matches_documented_retention(client, monkeypatch, account_row):
    """Regression: the history-expiry recovery window must not be shorter
    than Gmail's actual history retention (documented in sync.py as 'on the
    order of a week') — recovering fewer days than what's still retained
    means mail in that gap is unrecoverable once the cursor re-anchors."""
    db = get_db()
    db.execute('UPDATE email_accounts SET history_id=? WHERE id=?', ('stale', account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())

    def raise_expired(token, start_history_id, page_token=None):
        raise gmail_client.HistoryExpiredError('expired')

    captured = {}

    def fake_list(token, after_date, page_token=None):
        captured['after_date'] = after_date
        return {'messages': []}

    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')
    monkeypatch.setattr(gmail_client, 'list_history', raise_expired)
    monkeypatch.setattr(gmail_client, 'list_message_ids_since', fake_list)
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': 'me@example.com', 'historyId': 'h-fresh'})

    sync.sync_account(account_row)

    from datetime import date, timedelta
    assert sync.HISTORY_RECOVERY_DAYS >= 7
    expected_after_date = (date.today() - timedelta(days=sync.HISTORY_RECOVERY_DAYS)).strftime('%Y/%m/%d')
    assert captured['after_date'] == expected_after_date


def test_exception_is_recorded_and_never_raises(client, monkeypatch, account_row):
    def boom(db, row, cid, cs):
        raise RuntimeError('token refresh failed')

    monkeypatch.setattr(gmail_client, 'get_valid_access_token', boom)

    result = sync.sync_account(account_row)

    assert result == {'status': 'error', 'error': 'token refresh failed'}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['last_sync_error'] == 'token refresh failed'
