"""Losing and regaining an email sign-in: detection, the one announcement, the
scheduler standing down, and the phone's OAuth flow. No network."""
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from backend import app as app_module
from backend import auth, email_scheduler
from backend.db.connection import get_db
from backend.email import alerts, connection, gmail_client, imap_client, outlook_client, sync
from backend.push import notify
from backend.routes import email as routes


def account(refresh='rt-1', **over):
    now = int(time.time())
    row = dict(id='acct', provider='gmail', email_address='me@example.com', access_token='at', refresh_token=refresh,
               history_id='5', sync_enabled=1, created_at=now, updated_at=now, **over)
    db = get_db()
    db.execute(f"INSERT INTO email_accounts ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    db.commit()
    return fetch()


def fetch():
    return get_db().execute("SELECT * FROM email_accounts WHERE id='acct'").fetchone()


def google(status, reason):
    return gmail_client.GmailApiError('x', status_code=status, reason=reason)


def messages():
    return [json.loads(r['metadata'])['email'] for r in get_db().execute(
        "SELECT metadata FROM messages WHERE metadata LIKE '%\"email\"%'")]


@pytest.fixture
def oauth(client):
    client.patch('/api/settings/ai', json={'googleOauthClientId': 'cid', 'googleOauthClientSecret': 'sec'})
    return client


@pytest.mark.parametrize('exc,expected', [
    (google(400, 'invalid_grant'), True),
    (outlook_client.OutlookApiError('x', status_code=400, reason='invalid_grant'), True),
    (imap_client.ImapError('[AUTHENTICATIONFAILED] Invalid credentials'), True),
    (imap_client.ImapError('Could not connect to h:993: timed out'), False),
    (google(403, 'accessNotConfigured'), False),
    (google(401, 'UNAUTHENTICATED'), False),
    (google(429, None), False),
    (google(503, None), False),
    (requests.Timeout('slow'), False),
])
def test_only_a_refused_credential_counts(exc, expected):
    assert connection.is_auth_failure(exc, 'imap') is expected


def test_a_dead_token_is_marked_announced_once_and_left_alone(oauth, monkeypatch):
    row = account()
    sends = []
    monkeypatch.setattr(notify, 'send', lambda message, **kw: sends.append((message, kw)) or {'pushed': 1})

    def refuse(*a):
        raise google(400, 'invalid_grant')
    monkeypatch.setattr(sync, '_sync_gmail', refuse)

    assert sync.sync_account(row)['status'] == 'error'
    sync.sync_account(fetch())
    assert messages() == [{'kind': 'disconnected', 'provider': 'gmail', 'address': 'me@example.com'}]
    assert len(sends) == 1
    assert sends[0][1]['audience'] == 'email' and sends[0][0]['route'] == 'reconnect'
    assert email_scheduler._accounts_due(get_db()) == []
    state = oauth.get('/api/email/accounts').get_json()[0]
    assert state['needsReconnect'] is True and state['connected'] is False and state['authErrorAt']


def test_the_next_outage_is_announced_again(oauth, monkeypatch):
    row = account()
    connection.record_auth_failure(get_db(), row, google(400, 'invalid_grant'))
    monkeypatch.setattr(sync, '_sync_gmail', lambda *a: [])
    assert sync.sync_account(fetch())['status'] == 'ok'
    assert fetch()['auth_error_at'] is None
    connection.record_auth_failure(get_db(), fetch(), google(400, 'invalid_grant'))
    assert len(messages()) == 2


def test_a_transient_failure_is_retried(oauth, monkeypatch):
    row = account()

    def slow(*a):
        raise requests.Timeout('slow')
    monkeypatch.setattr(sync, '_sync_gmail', slow)
    sync.sync_account(row)
    assert fetch()['auth_error_at'] is None and messages() == []
    assert len(email_scheduler._accounts_due(get_db())) == 1


def test_a_401_drops_the_access_token_without_claiming_a_disconnect(oauth, monkeypatch):
    row = account()

    def stale(*a):
        raise google(401, 'UNAUTHENTICATED')
    monkeypatch.setattr(sync, '_sync_gmail', stale)
    sync.sync_account(row)
    assert fetch()['access_token'] is None and fetch()['auth_error_at'] is None


def test_outlook_refusing_the_login_is_only_a_stale_token(client, monkeypatch):
    client.patch('/api/settings/ai', json={'microsoftOauthClientId': 'cid', 'microsoftOauthClientSecret': 'sec'})
    account()
    get_db().execute("UPDATE email_accounts SET provider='outlook'")
    get_db().commit()

    def refuse(*a):
        raise imap_client.ImapError('AUTHENTICATE failed.')
    monkeypatch.setattr(sync, '_sync_imap', refuse)
    sync.sync_account(fetch())
    assert fetch()['access_token'] is None and fetch()['refresh_token'] == 'rt-1'
    assert fetch()['auth_error_at'] is None and messages() == []

    def dead(*a):
        raise outlook_client.OutlookApiError('x', status_code=400, reason='invalid_grant')
    monkeypatch.setattr(sync, '_sync_imap', dead)
    sync.sync_account(fetch())
    assert fetch()['auth_error_at'] and len(messages()) == 1


def test_a_failed_announcement_does_not_stop_the_sync_and_is_made_later(oauth, monkeypatch):
    account()

    def refuse(*a):
        raise google(400, 'invalid_grant')
    monkeypatch.setattr(sync, '_sync_gmail', refuse)

    def boom(*a):
        raise RuntimeError('database is locked')
    with monkeypatch.context() as m:
        m.setattr(alerts, 'post_message', boom)
        assert sync.sync_account(fetch())['status'] == 'error'
    assert fetch()['auth_error_at'] and fetch()['auth_notified_at'] is None and messages() == []
    sync.sync_account(fetch())
    assert len(messages()) == 1


def test_a_sync_from_before_the_reconnect_cannot_mark_it_dead(client):
    old = account()
    get_db().execute("UPDATE email_accounts SET refresh_token='rt-2' WHERE id='acct'")
    assert connection.record_auth_failure(get_db(), old, google(400, 'invalid_grant')) is False
    assert fetch()['auth_error_at'] is None and messages() == []


def test_the_message_survives_a_push_that_fails(client, monkeypatch):
    row = account()

    def boom(*a, **k):
        raise RuntimeError('apns down')
    monkeypatch.setattr(notify, 'send', boom)
    assert connection.record_auth_failure(get_db(), row, google(400, 'invalid_grant'))
    assert len(messages()) == 1


# ---------------------------------------------------------------- the phone

def start(client):
    url = client.post('/api/email/oauth/start', json={'provider': 'gmail', 'client': 'ios'}).get_json()['url']
    return parse_qs(urlparse(url).query)


def google_accepts(monkeypatch, address='me@example.com', **extra):
    used = []
    monkeypatch.setattr(gmail_client, 'exchange_code', lambda cid, sec, uri, code: used.append(uri) or {
        'access_token': 'at-2', 'refresh_token': 'rt-2', 'expires_in': 3600, **extra})
    monkeypatch.setattr(gmail_client, 'get_profile', lambda token: {'emailAddress': address})
    return used


def test_reconnect_keeps_the_account_and_clears_the_outage(oauth, monkeypatch):
    row = account()
    connection.record_auth_failure(get_db(), row, google(400, 'invalid_grant'))
    query = start(oauth)
    used = google_accepts(monkeypatch, refresh_token_expires_in=604799)
    resp = oauth.get('/api/email/oauth/callback', query_string={'state': query['state'][0], 'code': 'c'})
    assert resp.headers['Location'] == 'lunaschal://email?connected=1'
    assert used == query['redirect_uri']
    now = fetch()
    assert (now['refresh_token'], now['history_id'], now['auth_error_at'], now['auth_notified_at']) == ('rt-2', '5', None, None)
    state = oauth.get('/api/email/accounts').get_json()[0]
    assert state['connected'] is True and state['refreshExpiresAt']


def test_an_expiry_google_did_not_state_is_not_guessed(oauth, monkeypatch):
    query = start(oauth)
    google_accepts(monkeypatch)
    oauth.get('/api/email/oauth/callback', query_string={'state': query['state'][0], 'code': 'c'})
    assert oauth.get('/api/email/accounts').get_json()[0]['refreshExpiresAt'] is None


@pytest.mark.parametrize('params,location', [
    ({'error': 'access_denied'}, 'lunaschal://email?error=declined'),
    ({'error': '<script>'}, 'lunaschal://email?error=provider'),
    ({}, 'lunaschal://email?error=failed'),
])
def test_every_ending_returns_to_the_app_with_a_fixed_code(oauth, params, location):
    state = start(oauth)['state'][0]
    assert oauth.get('/api/email/oauth/callback', query_string={'state': state, **params}).headers['Location'] == location


def test_a_different_address_is_refused(oauth, monkeypatch):
    account()
    state = start(oauth)['state'][0]
    google_accepts(monkeypatch, address='other@example.com')
    resp = oauth.get('/api/email/oauth/callback', query_string={'state': state, 'code': 'c'})
    assert resp.headers['Location'] == 'lunaschal://email?error=other_account'
    assert get_db().execute('SELECT COUNT(*) c FROM email_accounts').fetchone()['c'] == 1


def test_a_state_works_once_and_expires(oauth, monkeypatch):
    google_accepts(monkeypatch)
    state = start(oauth)['state'][0]
    oauth.get('/api/email/oauth/callback', query_string={'state': state, 'code': 'c'})
    assert oauth.get('/api/email/oauth/callback', query_string={'state': state, 'code': 'c'}).status_code == 400
    late = start(oauth)['state'][0]
    real = time.time()
    monkeypatch.setattr(routes.time, 'time', lambda: real + 601)
    assert oauth.get('/api/email/oauth/callback', query_string={'state': late, 'code': 'c'}).status_code == 400


def test_only_the_callback_is_open_without_a_session(client, monkeypatch):
    # create_app reads these at call time, so patch them and build one.
    monkeypatch.setattr(auth, 'NETWORK_MODE', True)
    monkeypatch.setattr(auth, 'is_localhost', lambda request: False)
    oauth = app_module.create_app().test_client()
    assert oauth.post('/api/email/oauth/start', json={}).status_code == 401
    assert oauth.get('/api/email/oauth/authorize').status_code == 401
    assert oauth.get('/api/email').status_code == 401
    assert oauth.get('/api/email/oauth/callback').status_code == 400


def test_start_needs_an_oauth_client(client):
    assert client.post('/api/email/oauth/start', json={'provider': 'gmail'}).status_code == 400
    assert client.post('/api/email/oauth/start', json={'provider': 'imap'}).status_code == 400


def test_the_email_switch_is_its_own(client):
    token = 'ab' * 32
    client.post('/api/push/devices', json={'token': token, 'email': True})
    client.post('/api/push/devices', json={'token': token, 'coach': True})
    client.post('/api/push/devices', json={'token': token})
    row = get_db().execute('SELECT coach, email FROM push_devices').fetchone()
    assert (row['coach'], row['email']) == (1, 1)
    client.post('/api/push/devices', json={'token': token, 'coach': False})

    class Phone:
        sent = 0

        def send(self, *a, **k):
            Phone.sent += 1
            return type('R', (), {'ok': True})()
    assert notify.send({}, audience='coach', client=Phone())['pushed'] == 0
    assert notify.send({}, audience='email', client=Phone())['pushed'] == 1
    client.post('/api/push/devices', json={'token': token, 'email': False})
    assert alerts.push('t', 'b', route='inbox', collapse_id='x', client=Phone())['pushed'] == 0
