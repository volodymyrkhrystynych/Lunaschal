"""Thin Gmail API v1 + Google OAuth2 client: plain `requests` calls, no SDK.

Token exchange/refresh and every Gmail call used here are a handful of
authenticated HTTPS JSON requests, so this hand-rolls them rather than
pulling in google-auth/google-api-python-client — matches how the rest of
the codebase talks to external HTTP APIs (backend/newspapers/scraper.py,
backend/fanfic/download.py) and avoids fighting those libraries' own
credential-storage objects, since tokens here just live as plain SQLite
columns on email_accounts.
"""

import base64
import re
import time
from urllib.parse import urlencode

import requests

from backend.fanfic.sanitize import html_to_text

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
GMAIL_API_BASE = 'https://gmail.googleapis.com/gmail/v1'

# Read-only: this feature only ever mirrors mail locally, never sends or
# modifies anything in the mailbox.
SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'

_REQUEST_TIMEOUT = 15


class HistoryExpiredError(Exception):
    """Raised when Gmail's history cursor is too old (404 from history.list).

    Gmail only retains history records for a bounded window (on the order of
    a week) — callers should catch this and fall back to a full re-list of
    the mailbox, then re-baseline history_id from a fresh get_profile() call.
    Re-listing everything is safe and cheap: already-synced messages are
    skipped before the expensive per-message fetch (see
    backend/email/sync.py::_insert_message), so this can't create duplicates
    and only pays for messages that are actually new.
    """


def _auth_headers(access_token: str) -> dict:
    return {'Authorization': f'Bearer {access_token}'}


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        # offline + consent guarantees a refresh_token comes back even if the
        # user has authorized this app before.
        'access_type': 'offline',
        'prompt': 'consent',
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
    }, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def revoke_token(token: str) -> None:
    """Best-effort revoke — disconnect must still succeed locally if Google's
    revoke endpoint is unreachable or the token is already stale."""
    try:
        requests.post(REVOKE_URL, params={'token': token}, timeout=10)
    except requests.RequestException:
        pass


def get_valid_access_token(db, account_row: dict, client_id: str, client_secret: str) -> str:
    """Return a usable access token for the account, refreshing and
    persisting a new one if the current one is within 60s of expiry."""
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


def get_profile(access_token: str) -> dict:
    resp = requests.get(
        f'{GMAIL_API_BASE}/users/me/profile',
        headers=_auth_headers(access_token), timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def list_history(access_token: str, start_history_id: str, page_token: str | None = None) -> dict:
    params = {'startHistoryId': start_history_id, 'historyTypes': 'messageAdded', 'maxResults': 100}
    if page_token:
        params['pageToken'] = page_token
    resp = requests.get(
        f'{GMAIL_API_BASE}/users/me/history',
        headers=_auth_headers(access_token), params=params, timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 404:
        raise HistoryExpiredError('Gmail history cursor expired')
    resp.raise_for_status()
    return resp.json()


def list_all_message_ids(access_token: str, page_token: str | None = None) -> dict:
    """Lists every message in the mailbox, paginated 100 at a time. No `q` or
    `labelIds` filter: the goal is a complete local mirror for backup, so
    this deliberately doesn't bound by date. Gmail excludes SPAM/TRASH from
    an unfiltered listing by default (`includeSpamTrash` defaults to false)."""
    params = {'maxResults': 100}
    if page_token:
        params['pageToken'] = page_token
    resp = requests.get(
        f'{GMAIL_API_BASE}/users/me/messages',
        headers=_auth_headers(access_token), params=params, timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_message(access_token: str, gmail_id: str) -> dict:
    resp = requests.get(
        f'{GMAIL_API_BASE}/users/me/messages/{gmail_id}',
        headers=_auth_headers(access_token), params={'format': 'full'}, timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# --- pure parsing: no network, no DB — this is the unit-tested core ---

_SENDER_RE = re.compile(r'<([^>]+)>')


def _header(headers: list[dict], name: str) -> str | None:
    for h in headers:
        if h.get('name', '').lower() == name.lower():
            return h.get('value')
    return None


def _split_sender(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    match = _SENDER_RE.search(raw)
    if match:
        name = raw[:match.start()].strip().strip('"') or None
        return name, match.group(1).strip()
    # Bare address, no display name (e.g. "someone@example.com").
    return None, raw.strip()


def _decode_b64url(data: str) -> str:
    padded = data + '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode('utf-8', errors='replace')


def _walk_leaf_parts(payload: dict):
    """Yield (mimeType, base64url data) for every part carrying body data,
    recursing into multipart/* containers."""
    body = payload.get('body') or {}
    if body.get('data'):
        yield payload.get('mimeType', ''), body['data']
    for part in payload.get('parts') or []:
        yield from _walk_leaf_parts(part)


def _extract_body_text(payload: dict) -> str:
    plain, html = [], []
    for mime, data in _walk_leaf_parts(payload):
        decoded = _decode_b64url(data)
        if mime == 'text/plain':
            plain.append(decoded)
        elif mime == 'text/html':
            html.append(decoded)
    if plain:
        return '\n'.join(plain).strip()
    if html:
        return html_to_text('\n'.join(html))
    return ''


def parse_message(raw: dict) -> dict:
    """Gmail `users.messages.get?format=full` response -> the flat shape
    sync.py inserts into the `emails` table. Pure function: no network/DB."""
    payload = raw.get('payload') or {}
    headers = payload.get('headers') or []
    sender_name, sender_email = _split_sender(_header(headers, 'From'))
    internal_date = raw.get('internalDate')
    received_at = int(internal_date) // 1000 if internal_date else int(time.time())
    return {
        'gmailId': raw.get('id'),
        'threadId': raw.get('threadId'),
        'subject': _header(headers, 'Subject'),
        'sender': sender_name,
        'senderEmail': sender_email,
        'snippet': raw.get('snippet'),
        'bodyText': _extract_body_text(payload),
        'labelIds': raw.get('labelIds') or [],
        'receivedAt': received_at,
    }
