"""OAuth route tests for the Email feature. No real network calls — every
gmail_client/outlook_client HTTP call is monkeypatched, matching
test_newspapers.py's style."""
import base64
import json as _json
import time

import pytest

from backend.db.connection import get_db
from backend.email import gmail_client
from backend.routes.email import _get_account


def _fake_id_token(email: str) -> str:
    """A minimal unsigned JWT with the shape outlook_client.decode_id_token_email
    expects — header.payload.signature, payload base64url-encoded JSON."""
    payload = base64.urlsafe_b64encode(_json.dumps({'email': email}).encode()).decode().rstrip('=')
    return f'header.{payload}.sig'


def _set_oauth_client(client):
    client.patch('/api/settings/ai', json={
        'googleOauthClientId': 'client-123',
        'googleOauthClientSecret': 'secret-456',
    })


def test_get_account_prefers_most_recently_updated_over_created(client):
    """Regression: reconnecting an account is an UPDATE (the ON CONFLICT
    upsert in oauth_callback) that bumps updated_at but never touches
    created_at. _get_account() must resolve by updated_at, not created_at —
    otherwise reconnecting an older account after having connected and
    disconnected a newer-but-now-stale one would keep resolving to the
    stale account in oauth_status/oauth_disconnect/sync_now."""
    db = get_db()
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, refresh_token, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-a', 'gmail', 'a@example.com', 'rt-a', 1, 100, 500)"
    )
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, refresh_token, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-b', 'gmail', 'b@example.com', NULL, 0, 200, 300)"
    )
    db.commit()

    account = _get_account('gmail')

    assert account['id'] == 'acct-a'


def test_authorize_without_client_configured_is_400(client):
    resp = client.get('/api/email/oauth/authorize')
    assert resp.status_code == 400


def test_authorize_redirects_to_google(client, monkeypatch):
    _set_oauth_client(client)
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: f'https://accounts.google.com/mock?state={state}',
    )
    resp = client.get('/api/email/oauth/authorize')
    assert resp.status_code == 302
    assert resp.headers['Location'].startswith('https://accounts.google.com/mock?state=')


def test_callback_rejects_unknown_state(client):
    resp = client.get('/api/email/oauth/callback?state=bogus&code=abc')
    assert resp.status_code == 400


def test_callback_rejects_replayed_state(client, monkeypatch):
    _set_oauth_client(client)
    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600, 'scope': 's'},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'me@example.com', 'historyId': '1'},
    )
    state = captured['state']
    first = client.get(f'/api/email/oauth/callback?state={state}&code=abc')
    assert first.status_code == 302
    second = client.get(f'/api/email/oauth/callback?state={state}&code=abc')
    assert second.status_code == 400


def test_callback_success_upserts_account_and_redirects(client, monkeypatch):
    _set_oauth_client(client)
    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at1', 'refresh_token': 'rt1', 'expires_in': 3600, 'scope': 'gmail.readonly'},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'me@example.com', 'historyId': '1'},
    )
    resp = client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc123')
    assert resp.status_code == 302
    assert resp.headers['Location'] == '/'

    row = get_db().execute("SELECT * FROM email_accounts WHERE provider='gmail'").fetchone()
    assert row['email_address'] == 'me@example.com'
    assert row['access_token'] == 'at1'
    assert row['refresh_token'] == 'rt1'
    assert row['sync_enabled'] == 1
    assert row['history_id'] is None  # only set once sync.py actually backfills


def test_oauth_status_reflects_connection(client, monkeypatch):
    resp = client.get('/api/email/oauth/status')
    assert resp.get_json() == {'connected': False}

    _set_oauth_client(client)
    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600, 'scope': 's'},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'me@example.com', 'historyId': '1'},
    )
    client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc')

    resp = client.get('/api/email/oauth/status')
    body = resp.get_json()
    assert body['connected'] is True
    assert body['emailAddress'] == 'me@example.com'


def test_disconnect_is_soft_and_idempotent(client, monkeypatch):
    _set_oauth_client(client)
    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600, 'scope': 's'},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'me@example.com', 'historyId': '1'},
    )
    client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc')

    revoked = []
    monkeypatch.setattr('backend.routes.email.gmail_client.revoke_token', lambda t: revoked.append(t))

    resp = client.post('/api/email/oauth/disconnect')
    assert resp.get_json() == {'success': True}
    assert revoked == ['at']

    row = get_db().execute("SELECT * FROM email_accounts WHERE provider='gmail'").fetchone()
    assert row is not None  # row survives — this is a soft disconnect
    assert row['access_token'] is None
    assert row['refresh_token'] is None
    assert row['sync_enabled'] == 0

    # Disconnecting again when already disconnected is a no-op, not an error.
    resp2 = client.post('/api/email/oauth/disconnect')
    assert resp2.get_json() == {'success': True}


def test_disconnect_with_no_account_is_a_noop(client):
    resp = client.post('/api/email/oauth/disconnect')
    assert resp.get_json() == {'success': True}


# --- what the connect tab shows when Google says no ---


def _valid_state():
    from backend.routes.email import _new_state
    return _new_state('gmail')


def test_callback_shows_googles_reason_not_just_the_status_line(client, monkeypatch):
    """The reported failure: consent succeeds, then users/me/profile 403s
    because the Gmail API was never enabled on the project. The page has to
    carry Google's explanation — 'Forbidden for url: ...' names a problem
    the user cannot act on, and it reads identically for a declined scope."""
    _set_oauth_client(client)
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600},
    )

    def _boom(_token):
        raise gmail_client.GmailApiError(
            '403 PERMISSION_DENIED: Gmail API has not been used in project 12345 '
            'before or it is disabled.',
            status_code=403, reason='PERMISSION_DENIED',
        )

    monkeypatch.setattr('backend.routes.email.gmail_client.get_profile', _boom)

    resp = client.get(f'/api/email/oauth/callback?state={_valid_state()}&code=abc')

    assert resp.status_code == 502
    body = resp.get_data(as_text=True)
    assert 'has not been used in project 12345' in body
    assert _get_account('gmail') is None


def test_callback_escapes_the_provider_error_it_reflects(client):
    """`error` is a query parameter, so a crafted link controls it. It is
    echoed into an HTML page served from the app's own origin, so it must be
    escaped rather than interpolated raw."""
    resp = client.get('/api/email/oauth/callback?error=%3Cscript%3Ealert(1)%3C/script%3E')

    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert '<script>' not in body
    assert '&lt;script&gt;' in body


# --- multi-provider: accounts list, state->provider derivation, second-account guard ---


def test_list_accounts_reflects_each_provider_slot(client, monkeypatch):
    resp = client.get('/api/email/accounts')
    assert resp.get_json() == []

    _set_oauth_client(client)
    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'me@example.com'},
    )
    client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc')

    resp = client.get('/api/email/accounts')
    accounts = resp.get_json()
    assert len(accounts) == 1
    assert accounts[0] == {
        'provider': 'gmail', 'emailAddress': 'me@example.com', 'connected': True,
        'lastSyncedAt': None, 'lastSyncError': None, 'syncEnabled': True,
    }


def test_callback_derives_provider_from_state_not_url(client, monkeypatch):
    """The callback route takes no `provider` query param at all — the
    provider comes only from the server-trusted state that /authorize
    minted, so a state minted for one provider always drives that
    provider's own token exchange regardless of anything in the callback
    URL itself."""
    client.patch('/api/settings/ai', json={
        'microsoftOauthClientId': 'ms-client', 'microsoftOauthClientSecret': 'ms-secret',
    })
    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.outlook_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize?provider=outlook')

    gmail_called = []
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: gmail_called.append(1) or {},
    )
    outlook_called = []
    monkeypatch.setattr(
        'backend.routes.email.outlook_client.exchange_code',
        lambda *a, **k: outlook_called.append(1) or {
            'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600,
            'id_token': _fake_id_token('me@outlook.example'),
        },
    )

    resp = client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc')

    assert resp.status_code == 302
    assert outlook_called == [1]
    assert gmail_called == []
    row = get_db().execute("SELECT * FROM email_accounts WHERE provider='outlook'").fetchone()
    assert row['email_address'] == 'me@outlook.example'


def test_reconnecting_gmail_with_a_different_account_is_rejected(client, monkeypatch):
    _set_oauth_client(client)
    db = get_db()
    now = int(time.time())
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, refresh_token, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-a', 'gmail', 'first@example.com', 'rt', 1, ?, ?)",
        (now, now),
    )
    db.commit()

    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at2', 'refresh_token': 'rt2', 'expires_in': 3600},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'second@example.com'},
    )

    resp = client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc')

    assert resp.status_code == 409
    row = get_db().execute(
        "SELECT * FROM email_accounts WHERE provider='gmail' AND email_address='second@example.com'"
    ).fetchone()
    assert row is None
    # The original account is untouched.
    row_a = get_db().execute("SELECT * FROM email_accounts WHERE id='acct-a'").fetchone()
    assert row_a['refresh_token'] == 'rt'


def test_reconnecting_gmail_with_the_same_account_still_updates(client, monkeypatch):
    """The second-account guard only blocks a *different* address — refreshing
    consent for the same mailbox must keep working exactly as before."""
    _set_oauth_client(client)
    db = get_db()
    now = int(time.time())
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, refresh_token, sync_enabled, created_at, updated_at)"
        " VALUES ('acct-a', 'gmail', 'me@example.com', 'rt-old', 1, ?, ?)",
        (now, now),
    )
    db.commit()

    captured = {}
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.build_auth_url',
        lambda client_id, redirect_uri, state: captured.setdefault('state', state) or 'https://x',
    )
    client.get('/api/email/oauth/authorize')
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.exchange_code',
        lambda *a, **k: {'access_token': 'at-new', 'refresh_token': 'rt-new', 'expires_in': 3600},
    )
    monkeypatch.setattr(
        'backend.routes.email.gmail_client.get_profile',
        lambda token: {'emailAddress': 'me@example.com'},
    )

    resp = client.get(f'/api/email/oauth/callback?state={captured["state"]}&code=abc')

    assert resp.status_code == 302
    row = get_db().execute("SELECT * FROM email_accounts WHERE provider='gmail'").fetchone()
    assert row['refresh_token'] == 'rt-new'
