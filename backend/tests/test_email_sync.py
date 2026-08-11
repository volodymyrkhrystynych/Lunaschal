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
        gmail_client, 'list_all_message_ids',
        lambda token, page_token=None: {'messages': [{'id': 'm1'}, {'id': 'm2'}]},
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


def test_first_connect_pages_through_the_whole_mailbox(client, monkeypatch, account_row):
    """First connect must not stop at one page or apply any date bound — the
    goal is a complete local mirror, so it keeps paging until nextPageToken
    runs out, however far back that goes."""
    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')
    pages = {
        None: {'messages': [{'id': 'm1'}], 'nextPageToken': 'p2'},
        'p2': {'messages': [{'id': 'm2'}], 'nextPageToken': 'p3'},
        'p3': {'messages': [{'id': 'm3'}]},
    }
    monkeypatch.setattr(
        gmail_client, 'list_all_message_ids',
        lambda token, page_token=None: pages[page_token],
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': 'me@example.com', 'historyId': 'h100'})

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 3}
    rows = get_db().execute('SELECT gmail_id FROM emails WHERE account_id=?', (account_row['id'],)).fetchall()
    assert {r['gmail_id'] for r in rows} == {'m1', 'm2', 'm3'}


def test_rerunning_backfill_is_idempotent(client, monkeypatch, account_row):
    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')
    monkeypatch.setattr(
        gmail_client, 'list_all_message_ids',
        lambda token, page_token=None: {'messages': [{'id': 'm1'}]},
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


def test_expired_history_cursor_falls_back_to_full_relist(client, monkeypatch, account_row):
    """Recovery from an expired history cursor must re-list the entire
    mailbox (no date bound), so mail older than any short recovery window
    is still recovered — proven here with a message that would fall outside
    a short recency window but still gets picked up."""
    db = get_db()
    db.execute('UPDATE email_accounts SET history_id=? WHERE id=?', ('stale', account_row['id']))
    db.commit()
    account_row = dict(db.execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone())

    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')

    def raise_expired(token, start_history_id, page_token=None):
        raise gmail_client.HistoryExpiredError('expired')

    monkeypatch.setattr(gmail_client, 'list_history', raise_expired)
    monkeypatch.setattr(
        gmail_client, 'list_all_message_ids',
        lambda token, page_token=None: {'messages': [{'id': 'ancient-message'}]},
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': 'me@example.com', 'historyId': 'h-fresh'})

    result = sync.sync_account(account_row)

    assert result == {'status': 'ok', 'newCount': 1}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['history_id'] == 'h-fresh'
    row = get_db().execute('SELECT gmail_id FROM emails WHERE account_id=?', (account_row['id'],)).fetchone()
    assert row['gmail_id'] == 'ancient-message'


def test_backfill_snapshots_history_id_before_paging_messages(client, monkeypatch, account_row):
    """Regression: get_profile (the history_id baseline) must be called
    before list_all_message_ids starts paginating, not after. A message
    arriving mid-backfill would already be missed by pagination (newest
    first) but, if the baseline were snapshotted afterwards, would also fall
    at-or-before that baseline and so be silently skipped by every future
    incremental sync too."""
    call_order = []
    monkeypatch.setattr(gmail_client, 'get_valid_access_token', lambda db, row, cid, cs: 'token123')

    def fake_get_profile(token):
        call_order.append('get_profile')
        return {'emailAddress': 'me@example.com', 'historyId': 'h100'}

    def fake_list_all_message_ids(token, page_token=None):
        call_order.append('list_all_message_ids')
        return {'messages': [{'id': 'm1'}]}

    monkeypatch.setattr(gmail_client, 'get_profile', fake_get_profile)
    monkeypatch.setattr(gmail_client, 'list_all_message_ids', fake_list_all_message_ids)
    monkeypatch.setattr(gmail_client, 'get_message', lambda token, gid: _gmail_message(gid))

    sync.sync_account(account_row)

    assert call_order[0] == 'get_profile'


def test_exception_is_recorded_and_never_raises(client, monkeypatch, account_row):
    def boom(db, row, cid, cs):
        raise RuntimeError('token refresh failed')

    monkeypatch.setattr(gmail_client, 'get_valid_access_token', boom)

    result = sync.sync_account(account_row)

    assert result == {'status': 'error', 'error': 'token refresh failed'}
    updated = get_db().execute('SELECT * FROM email_accounts WHERE id=?', (account_row['id'],)).fetchone()
    assert updated['last_sync_error'] == 'token refresh failed'


# --- HTML, images, concurrency, and when classification is queued ---


def _html_message(gmail_id, html):
    import base64
    return {
        'id': gmail_id, 'threadId': f'thread-{gmail_id}', 'labelIds': ['INBOX'], 'snippet': 's',
        'internalDate': '1700000000000',
        'payload': {
            'headers': [{'name': 'Subject', 'value': 'HTML mail'}],
            'mimeType': 'text/html',
            'body': {'data': base64.urlsafe_b64encode(html.encode()).decode()},
        },
    }


def test_html_is_stored_sanitized_and_images_are_queued(client, monkeypatch, account_row):
    html = '<p onclick="x()">Hello<img src="https://cdn.example/logo.png"></p>'
    monkeypatch.setattr(gmail_client, 'get_profile', lambda t: {'historyId': '9'})
    monkeypatch.setattr(
        gmail_client, 'list_all_message_ids',
        lambda t, p=None: {'messages': [{'id': 'g1'}]},
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda t, gid: _html_message(gid, html))

    sync.sync_account(account_row)

    row = get_db().execute('SELECT body_html, body_text FROM emails').fetchone()
    assert 'onclick' not in row['body_html']
    assert 'Hello' in row['body_html']
    # Rewritten to a local path, never left pointing at the sender.
    assert 'cdn.example' not in row['body_html']
    assert '/api/email/images/' in row['body_html']
    # And the fetch was queued for the background worker.
    queued = get_db().execute('SELECT url, status FROM email_images').fetchall()
    assert [(r['url'], r['status']) for r in queued] == [
        ('https://cdn.example/logo.png', 'pending')
    ]


def test_plain_text_mail_stores_empty_html(client, monkeypatch, account_row):
    monkeypatch.setattr(gmail_client, 'get_profile', lambda t: {'historyId': '9'})
    monkeypatch.setattr(
        gmail_client, 'list_all_message_ids', lambda t, p=None: {'messages': [{'id': 'g1'}]}
    )
    monkeypatch.setattr(gmail_client, 'get_message', lambda t, gid: _gmail_message(gid))

    sync.sync_account(account_row)

    row = get_db().execute('SELECT body_html, body_text FROM emails').fetchone()
    assert row['body_html'] == ''
    assert row['body_text'] == 'body text'


def test_classification_is_queued_per_message_during_the_walk(
    client, monkeypatch, account_row, run_bg_sync
):
    """Regression: the enqueue used to happen once, after the whole sync
    returned. On a multi-hour first backfill that left every category NULL
    until the last page landed, and a crash mid-walk discarded the queue for
    everything already imported."""
    seen_during_walk = []

    def _list(token, page_token=None):
        if page_token is None:
            return {'messages': [{'id': 'g1'}], 'nextPageToken': 'p2'}
        # By the time the second page is requested, page one's message must
        # already have been handed to the classifier.
        seen_during_walk.append(list(run_bg_sync))
        return {'messages': [{'id': 'g2'}]}

    monkeypatch.setattr(gmail_client, 'get_profile', lambda t: {'historyId': '9'})
    monkeypatch.setattr(gmail_client, 'list_all_message_ids', _list)
    monkeypatch.setattr(gmail_client, 'get_message', lambda t, gid: _gmail_message(gid))

    sync.sync_account(account_row)

    assert len(seen_during_walk[0]) == 1, 'page 1 was not classified before page 2 was fetched'
    assert len(run_bg_sync) == 2


def test_a_second_concurrent_sync_is_refused_rather_than_racing(
    client, monkeypatch, account_row
):
    """Regression for `UNIQUE constraint failed: emails.account_id,
    emails.gmail_id`. The scheduler treats an account as due for the whole
    time last_synced_at IS NULL — the entire backfill — so a manual sync
    landing mid-walk started a second one over the same mailbox."""
    reentered = []

    def _list(token, page_token=None):
        # Re-enter exactly as the scheduler would, from inside the first run.
        reentered.append(sync.sync_account(account_row))
        return {'messages': [{'id': 'g1'}]}

    monkeypatch.setattr(gmail_client, 'get_profile', lambda t: {'historyId': '9'})
    monkeypatch.setattr(gmail_client, 'list_all_message_ids', _list)
    monkeypatch.setattr(gmail_client, 'get_message', lambda t, gid: _gmail_message(gid))

    result = sync.sync_account(account_row)

    assert reentered == [{'status': 'busy', 'newCount': 0}]
    assert result['status'] == 'ok'
    assert get_db().execute('SELECT COUNT(*) c FROM emails').fetchone()['c'] == 1


def test_a_duplicate_gmail_id_does_not_abort_the_sync(client, monkeypatch, account_row):
    """Belt to the lock's braces: even if two writers get past the SELECT,
    the unique index must resolve it as 'already have it', not an
    IntegrityError that kills the run and discards its progress."""
    db = get_db()

    def _get_message(token, gmail_id):
        # Lose the race precisely: _insert_message's SELECT has already run
        # and found nothing, and the competing writer commits the row while
        # we are still fetching. Only the unique index can catch this.
        if gmail_id == 'g1':
            now = int(time.time())
            db.execute(
                'INSERT INTO emails (id, account_id, gmail_id, body_text, received_at, created_at)'
                " VALUES ('other-writer', ?, 'g1', 'x', ?, ?)",
                (account_row['id'], now, now),
            )
            db.commit()
        return _gmail_message(gmail_id)

    monkeypatch.setattr(gmail_client, 'get_profile', lambda t: {'historyId': '9'})
    monkeypatch.setattr(
        gmail_client, 'list_all_message_ids',
        lambda t, p=None: {'messages': [{'id': 'g1'}, {'id': 'g2'}]},
    )
    monkeypatch.setattr(gmail_client, 'get_message', _get_message)

    result = sync.sync_account(account_row)

    # The run survived the conflict and went on to import g2.
    assert result['status'] == 'ok'
    assert result['newCount'] == 1
    assert db.execute('SELECT COUNT(*) c FROM emails').fetchone()['c'] == 2
    assert db.execute("SELECT id FROM emails WHERE gmail_id='g1'").fetchone()['id'] == 'other-writer'
