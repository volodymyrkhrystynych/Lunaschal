"""Tests for POST /api/email/imap/connect — the generic-IMAP connect flow,
which (unlike gmail/outlook) has no OAuth redirect dance: it validates by
actually connecting, then persists. imap_client.connect is monkeypatched —
no real sockets — matching test_email_oauth_routes.py's style."""
from backend.db.connection import get_db
from backend.email import imap_client


class _StubConn:
    def select(self, folder, readonly=False):
        pass

    def logout(self):
        pass


def _patch_successful_connect(monkeypatch):
    monkeypatch.setattr(imap_client, 'connect', lambda *a, **k: _StubConn())
    monkeypatch.setattr(imap_client, 'folder_status', lambda conn: {'uidValidity': 1, 'uidNext': 1})


_VALID_BODY = {
    'host': 'imap.fastmail.com', 'port': 993, 'username': 'me@fastmail.example',
    'password': 'app-pass', 'emailAddress': 'me@fastmail.example',
}


def test_missing_fields_is_400(client):
    resp = client.post('/api/email/imap/connect', json={'host': 'imap.example.com'})
    assert resp.status_code == 400


def test_failed_connect_surfaces_the_servers_error(client, monkeypatch):
    def _boom(*a, **k):
        raise imap_client.ImapError('AUTHENTICATIONFAILED: Invalid credentials')

    monkeypatch.setattr(imap_client, 'connect', _boom)

    resp = client.post('/api/email/imap/connect', json=_VALID_BODY)

    assert resp.status_code == 400
    assert 'Invalid credentials' in resp.get_json()['error']
    assert get_db().execute("SELECT * FROM email_accounts WHERE provider='imap'").fetchone() is None


def test_success_upserts_an_imap_account(client, monkeypatch):
    _patch_successful_connect(monkeypatch)

    resp = client.post('/api/email/imap/connect', json=_VALID_BODY)

    assert resp.status_code == 200
    assert resp.get_json() == {'success': True}
    row = get_db().execute("SELECT * FROM email_accounts WHERE provider='imap'").fetchone()
    assert row['email_address'] == 'me@fastmail.example'
    assert row['imap_host'] == 'imap.fastmail.com'
    assert row['imap_port'] == 993
    assert row['imap_username'] == 'me@fastmail.example'
    assert row['imap_password'] == 'app-pass'
    assert row['sync_enabled'] == 1


def test_reconnect_with_new_password_updates_in_place(client, monkeypatch):
    _patch_successful_connect(monkeypatch)
    client.post('/api/email/imap/connect', json=_VALID_BODY)

    updated_body = dict(_VALID_BODY, password='new-app-pass')
    resp = client.post('/api/email/imap/connect', json=updated_body)

    assert resp.status_code == 200
    rows = get_db().execute("SELECT * FROM email_accounts WHERE provider='imap'").fetchall()
    assert len(rows) == 1
    assert rows[0]['imap_password'] == 'new-app-pass'


def test_connecting_a_second_different_account_is_rejected(client, monkeypatch):
    _patch_successful_connect(monkeypatch)
    client.post('/api/email/imap/connect', json=_VALID_BODY)

    other_body = dict(_VALID_BODY, emailAddress='someone-else@example.com', username='someone-else@example.com')
    resp = client.post('/api/email/imap/connect', json=other_body)

    assert resp.status_code == 409
    rows = get_db().execute("SELECT * FROM email_accounts WHERE provider='imap'").fetchall()
    assert len(rows) == 1
    assert rows[0]['email_address'] == 'me@fastmail.example'


def test_invalid_port_is_400(client):
    resp = client.post('/api/email/imap/connect', json=dict(_VALID_BODY, port='not-a-number'))
    assert resp.status_code == 400
