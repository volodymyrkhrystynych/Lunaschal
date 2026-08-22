"""Microsoft identity platform OAuth2 client for Outlook.

Outlook is connected over IMAP (backend/email/imap_client.py), not Microsoft
Graph — this module only does the OAuth2 token dance (authorization-code
exchange, refresh) needed to get a bearer token for IMAP's AUTHENTICATE
XOAUTH2. Shaped exactly like gmail_client.py's auth section: plain
`requests` calls, no MSAL SDK, tokens stored as plain SQLite columns on
email_accounts (same threat model as Gmail's — single-user local app, no
at-rest secret encryption anywhere else).
"""

import base64
import json as _json
import time
from urllib.parse import urlencode

import requests

AUTH_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize'
TOKEN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'

# IMAP.AccessAsUser.All: read-only mirror (this feature never sends or
# modifies mail); offline_access: required for a refresh_token to come back.
# openid+email+profile: this app talks IMAP, not Graph, so there's no
# profile endpoint to ask "whose mailbox is this" — the id_token's email
# claim (decoded below) is the only source for email_accounts.email_address.
SCOPE = 'https://outlook.office.com/IMAP.AccessAsUser.All offline_access openid email profile'

IMAP_HOST = 'outlook.office365.com'
IMAP_PORT = 993

_REQUEST_TIMEOUT = 15


class OutlookApiError(requests.HTTPError):
    """An HTTP error from Microsoft that keeps the message it actually sent.

    Mirrors gmail_client.GmailApiError — Microsoft's error body shape is
    {'error': 'invalid_grant', 'error_description': '...'}, the distinguishing
    detail lands in the response body, and str(e) here is what a failed
    connect/sync shows (routes/email.py::oauth_callback, last_sync_error).
    """

    def __init__(self, message: str, *, status_code: int, reason: str | None, response=None):
        super().__init__(message, response=response)
        self.status_code = status_code
        self.reason = reason


def _error_detail(resp) -> tuple[str | None, str | None]:
    try:
        body = resp.json()
    except ValueError:
        text = (resp.text or '').strip()
        return (text[:300] or None), None
    if not isinstance(body, dict):
        return None, None
    return body.get('error_description') or body.get('error'), body.get('error')


def _raise_for_status(resp) -> None:
    if resp.status_code < 400:
        return
    message, reason = _error_detail(resp)
    head = f'{resp.status_code} {reason}' if reason else str(resp.status_code)
    raise OutlookApiError(
        f'{head}: {message}' if message else f'{head} from {resp.url}',
        status_code=resp.status_code,
        reason=reason,
        response=resp,
    )


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'response_mode': 'query',
        'scope': SCOPE,
        'state': state,
    }
    return f'{AUTH_URL}?{urlencode(params)}'


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'code': code,
        'grant_type': 'authorization_code',
        'scope': SCOPE,
    }, timeout=_REQUEST_TIMEOUT)
    _raise_for_status(resp)
    return resp.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
        'scope': SCOPE,
    }, timeout=_REQUEST_TIMEOUT)
    _raise_for_status(resp)
    return resp.json()


def revoke_token(token: str) -> None:
    """No-op: unlike Google, Microsoft's v2.0 endpoint has no public
    server-side token-revocation REST call. Disconnecting an Outlook account
    can only clear the locally-stored tokens — the Settings UI's disconnect
    copy says as much, pointing the user at their Microsoft account's app
    permissions page for a real revoke."""


def decode_id_token_email(id_token: str) -> str | None:
    """Best-effort extraction of the connected mailbox's address from the
    OAuth id_token's email/preferred_username claim. No signature
    verification — this app only ever uses the value to seed
    email_accounts.email_address for the account the user just consented
    for in this same request; it is never used for an auth decision."""
    if not id_token or id_token.count('.') != 2:
        return None
    try:
        payload_b64 = id_token.split('.')[1]
        padded = payload_b64 + '=' * (-len(payload_b64) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    return payload.get('email') or payload.get('preferred_username')


def get_valid_access_token(db, account_row: dict, client_id: str, client_secret: str) -> str:
    """Return a usable access token for the account, refreshing and
    persisting a new one if the current one is within 60s of expiry. Mirrors
    gmail_client.get_valid_access_token exactly."""
    now = int(time.time())
    expires_at = account_row.get('token_expires_at')
    if account_row.get('access_token') and expires_at and expires_at > now + 60:
        return account_row['access_token']
    data = refresh_access_token(client_id, client_secret, account_row['refresh_token'])
    expires_at = now + int(data.get('expires_in', 3600))
    db.execute(
        'UPDATE email_accounts SET access_token=?, token_expires_at=?, updated_at=? WHERE id=?',
        (data['access_token'], expires_at, now, account_row['id']),
    )
    db.commit()
    return data['access_token']
