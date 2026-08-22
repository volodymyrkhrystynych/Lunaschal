"""Network-free tests for backend/email/outlook_client.py: the Microsoft
identity platform OAuth2 token dance, its error surfacing, and the id_token
email-claim decoding this app uses in place of a Graph profile call (Outlook
is connected over IMAP here, not Graph). Same style as test_gmail_client.py:
requests.post is monkeypatched, no real network.
"""
import base64
import json

import pytest
import requests

from backend.email import outlook_client


class _FakeResponse:
    """Enough of requests.Response for _raise_for_status."""

    def __init__(self, status_code, body=None, text=''):
        self.status_code = status_code
        self._body = body
        self.text = text if body is None else json.dumps(body)
        self.url = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


def test_build_auth_url_includes_client_scope_and_state():
    url = outlook_client.build_auth_url('client-1', 'https://app.example/callback', 'state-xyz')
    assert url.startswith(outlook_client.AUTH_URL + '?')
    assert 'client_id=client-1' in url
    assert 'state=state-xyz' in url
    assert 'IMAP.AccessAsUser.All' in url
    assert 'offline_access' in url


def test_exchange_code_posts_authorization_code_grant(monkeypatch):
    captured = {}

    def fake_post(url, data, timeout):
        captured['url'] = url
        captured['data'] = data
        return _FakeResponse(200, {'access_token': 'at', 'refresh_token': 'rt', 'expires_in': 3600})

    monkeypatch.setattr(outlook_client.requests, 'post', fake_post)

    result = outlook_client.exchange_code('cid', 'csecret', 'https://app/callback', 'code123')

    assert captured['url'] == outlook_client.TOKEN_URL
    assert captured['data']['grant_type'] == 'authorization_code'
    assert captured['data']['code'] == 'code123'
    assert result['access_token'] == 'at'


def test_refresh_access_token_posts_refresh_grant(monkeypatch):
    captured = {}

    def fake_post(url, data, timeout):
        captured['data'] = data
        return _FakeResponse(200, {'access_token': 'at2', 'expires_in': 3600})

    monkeypatch.setattr(outlook_client.requests, 'post', fake_post)

    result = outlook_client.refresh_access_token('cid', 'csecret', 'refresh-tok')

    assert captured['data']['grant_type'] == 'refresh_token'
    assert captured['data']['refresh_token'] == 'refresh-tok'
    assert result['access_token'] == 'at2'


def test_token_endpoint_error_keeps_microsofts_explanation(monkeypatch):
    monkeypatch.setattr(
        outlook_client.requests, 'post',
        lambda url, data, timeout: _FakeResponse(400, {
            'error': 'invalid_grant',
            'error_description': 'AADSTS70000: The refresh token has expired.',
        }),
    )
    with pytest.raises(outlook_client.OutlookApiError) as excinfo:
        outlook_client.refresh_access_token('cid', 'csecret', 'stale-token')
    assert 'refresh token has expired' in str(excinfo.value)
    assert excinfo.value.reason == 'invalid_grant'


def test_non_json_error_body_falls_back_to_truncated_text(monkeypatch):
    monkeypatch.setattr(
        outlook_client.requests, 'post',
        lambda url, data, timeout: _FakeResponse(502, body=None, text='<html>' + 'x' * 500 + '</html>'),
    )
    with pytest.raises(outlook_client.OutlookApiError) as excinfo:
        outlook_client.exchange_code('cid', 'cs', 'https://app/callback', 'code')
    assert len(str(excinfo.value)) < 400


def test_outlook_api_error_is_caught_as_a_requests_error():
    assert issubclass(outlook_client.OutlookApiError, requests.RequestException)


def test_revoke_token_is_a_documented_noop():
    # Must not raise — there's no server-side revoke endpoint on Microsoft's
    # v2.0 platform, unlike Google's, so disconnect can only clear local
    # tokens.
    assert outlook_client.revoke_token('any-token') is None


# --- id_token email decoding: this app talks IMAP, not Graph, so this is
# the only source for the connected mailbox's address ---


def _fake_id_token(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'header.{encoded}.sig'


def test_decode_id_token_email_prefers_email_claim():
    token = _fake_id_token({'email': 'me@outlook.example', 'preferred_username': 'other@outlook.example'})
    assert outlook_client.decode_id_token_email(token) == 'me@outlook.example'


def test_decode_id_token_email_falls_back_to_preferred_username():
    token = _fake_id_token({'preferred_username': 'me@outlook.example'})
    assert outlook_client.decode_id_token_email(token) == 'me@outlook.example'


def test_decode_id_token_email_returns_none_for_malformed_token():
    assert outlook_client.decode_id_token_email('not-a-jwt') is None
    assert outlook_client.decode_id_token_email('') is None


# --- get_valid_access_token: refresh-if-near-expiry, persisted back ---


class _FakeDb:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def commit(self):
        pass


def test_get_valid_access_token_reuses_unexpired_token(monkeypatch):
    db = _FakeDb()
    called = []
    monkeypatch.setattr(outlook_client, 'refresh_access_token', lambda *a: called.append(1) or {})
    account_row = {'access_token': 'still-good', 'token_expires_at': 10_000_000_000, 'id': 'acc1'}

    token = outlook_client.get_valid_access_token(db, account_row, 'cid', 'cs')

    assert token == 'still-good'
    assert called == []


def test_get_valid_access_token_refreshes_when_near_expiry(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(
        outlook_client, 'refresh_access_token',
        lambda cid, cs, rt: {'access_token': 'fresh-token', 'expires_in': 3600},
    )
    account_row = {
        'access_token': 'stale', 'token_expires_at': 1, 'refresh_token': 'rt', 'id': 'acc1',
    }

    token = outlook_client.get_valid_access_token(db, account_row, 'cid', 'cs')

    assert token == 'fresh-token'
    assert db.executed  # persisted back to email_accounts
