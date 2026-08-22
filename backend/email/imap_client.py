"""Stdlib IMAP client (imaplib + email) shared by the Outlook and generic-IMAP
email connectors — no new dependency, matching how gmail_client.py hand-rolls
its own HTTP calls instead of pulling in a provider SDK.

Outlook and generic IMAP differ only in how connect() authenticates (an
Outlook access token via AUTHENTICATE XOAUTH2, or a generic account's
password via LOGIN) and in whether the host/port are the fixed Outlook
constants (backend/email/outlook_client.py) or user-supplied — everything
below this point (UID walking, message parsing) is identical for both, so
backend/email/sync.py's IMAP sync path uses this module for either provider.

v1 syncs INBOX only, mirroring gmail_client's "one full-mailbox mirror"
philosophy applied to the one folder IMAP calls the mailbox by default.
Multi-folder sync is a genuine future migration (folder becomes part of the
sync cursor), not something worth a placeholder column now.
"""

import email
import email.policy
import email.utils
import imaplib
import time

from backend.fanfic.sanitize import html_to_text

INBOX = 'INBOX'

_TIMEOUT = 30


class ImapError(Exception):
    """An IMAP NO/BAD response, or a connection failure, with the server's
    own text kept — same reasoning as gmail_client.GmailApiError: the
    message is what a failed connect/sync shows the user, so throwing away
    the server's explanation throws away the only part that says what to do
    next."""


def _xoauth2_string(username: str, access_token: str) -> str:
    return f'user={username}\x01auth=Bearer {access_token}\x01\x01'


def connect(
    host: str, port: int, *, username: str,
    password: str | None = None, access_token: str | None = None,
) -> imaplib.IMAP4_SSL:
    """Open and authenticate an IMAP connection. Exactly one of `password` /
    `access_token` must be given: `password` for a generic IMAP account
    (plain LOGIN), `access_token` for Outlook (AUTHENTICATE XOAUTH2)."""
    if bool(password) == bool(access_token):
        raise ValueError('connect() needs exactly one of password or access_token')
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=_TIMEOUT)
        if access_token:
            auth_string = _xoauth2_string(username, access_token)
            conn.authenticate('XOAUTH2', lambda _: auth_string.encode())
        else:
            conn.login(username, password)
    except imaplib.IMAP4.error as e:
        raise ImapError(str(e)) from e
    except OSError as e:
        raise ImapError(f'Could not connect to {host}:{port}: {e}') from e
    return conn


def _check(label: str, status: str, data) -> None:
    if status != 'OK':
        detail = data[0].decode('utf-8', 'replace') if data and data[0] else status
        raise ImapError(f'{label} failed: {detail}')


def folder_status(conn: imaplib.IMAP4_SSL, folder: str = INBOX) -> dict:
    status, data = conn.status(folder, '(UIDVALIDITY UIDNEXT)')
    _check('STATUS', status, data)
    raw = (data[0] or b'').decode('utf-8', 'replace')
    fields = raw[raw.index('(') + 1: raw.rindex(')')].split()
    parsed = dict(zip(fields[0::2], fields[1::2]))
    return {'uidValidity': int(parsed['UIDVALIDITY']), 'uidNext': int(parsed['UIDNEXT'])}


def search_all_uids(conn: imaplib.IMAP4_SSL) -> list[int]:
    """Full-mailbox listing — used for first connect and whenever
    UIDVALIDITY has changed (IMAP's equivalent of Gmail's history cursor
    expiring: once it changes, every previously-remembered UID for this
    mailbox is meaningless)."""
    status, data = conn.uid('search', None, 'ALL')
    _check('UID SEARCH', status, data)
    return [int(x) for x in data[0].split()] if data and data[0] else []


def search_uids_since(conn: imaplib.IMAP4_SSL, uid_next: int) -> list[int]:
    """Everything at or after `uid_next` — the standard IMAP idiom for
    incremental sync (RFC 3501 `n:*`)."""
    status, data = conn.uid('search', None, f'UID {uid_next}:*')
    _check('UID SEARCH', status, data)
    if not data or not data[0]:
        return []
    uids = [int(x) for x in data[0].split()]
    # RFC 3501: a range ending in * whose start is beyond the highest UID
    # still returns that highest UID once. Filtering it out here means an
    # already-known message never reaches the caller at all, rather than
    # relying on _insert_imap_message's existence check to no-op on it.
    return [u for u in uids if u >= uid_next]


def fetch_message(conn: imaplib.IMAP4_SSL, uid: int) -> bytes | None:
    # BODY.PEEK[] rather than RFC822: PEEK never sets \Seen, matching the
    # gmail.readonly scope's "never modify the mailbox" contract.
    status, data = conn.uid('fetch', str(uid), '(BODY.PEEK[])')
    _check('UID FETCH', status, data)
    for part in data:
        if isinstance(part, tuple) and len(part) == 2:
            return part[1]
    # OK status but no data: the message was deleted between SEARCH and
    # FETCH. Routine for a mailbox-wide mirror (mirrors gmail_client's 404
    # handling), not a sync failure — the caller treats None as "skip it".
    return None


# --- pure parsing: no network, no DB — this is the unit-tested core ---

def _extract_bodies(msg: email.message.Message) -> tuple[str, str]:
    """Return (plain text, raw html), same plain-preferred-else-html
    fallback as gmail_client._extract_bodies."""
    plain, html = [], []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type == 'text/plain':
            plain.append(part.get_content())
        elif content_type == 'text/html':
            html.append(part.get_content())
    raw_html = '\n'.join(html)
    if plain:
        return '\n'.join(plain).strip(), raw_html
    if html:
        return html_to_text(raw_html), raw_html
    return '', ''


def parse_message(raw: bytes, uid: int) -> dict:
    """Raw RFC822 bytes from fetch_message() -> the flat shape sync.py
    inserts into the `emails` table. Pure function: no network/DB. Shape
    matches gmail_client.parse_message so sync.py's insert path doesn't
    branch on provider.

    email.policy.default auto-decodes RFC2047 encoded-word headers and
    charset bodies, so msg['Subject']/msg['From'] and part.get_content() come
    back as plain str already — no hand-rolled header/charset decoding
    needed the way gmail_client.py needs for Gmail's raw header strings.
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender_name, sender_email = email.utils.parseaddr(msg.get('From', ''))
    date_header = msg.get('Date')
    received_at = int(time.time())
    if date_header:
        try:
            parsed_date = email.utils.parsedate_to_datetime(date_header)
            if parsed_date is not None:
                received_at = int(parsed_date.timestamp())
        except (TypeError, ValueError):
            pass
    body_text, body_html = _extract_bodies(msg)
    return {
        'providerMessageId': str(uid),
        'threadId': None,
        'subject': msg.get('Subject'),
        'sender': sender_name or None,
        'senderEmail': sender_email or None,
        'snippet': (body_text[:200] or None) if body_text else None,
        'bodyText': body_text,
        'bodyHtml': body_html,
        'labelIds': [],
        'receivedAt': received_at,
    }
