"""Network-free tests for backend/email/imap_client.py: message parsing,
the small IMAP response parsers (STATUS/SEARCH/FETCH), and the XOAUTH2
string format — matching test_gmail_client.py's style (inline fixtures, no
real network/sockets). connect() itself is exercised only for its
password-xor-access_token validation, since anything past that needs a real
socket.
"""
import time

import pytest
from email.message import EmailMessage

from backend.email import imap_client
from backend.email.imap_client import _xoauth2_string, parse_message

_DATE = 'Tue, 14 Nov 2023 22:13:20 +0000'


def _message(**headers) -> bytes:
    body = headers.pop('_body', 'Thanks for applying!')
    msg = EmailMessage()
    msg['Subject'] = headers.pop('Subject', 'Hi')
    msg['From'] = headers.pop('From', 'Acme Recruiting <recruiting@acme.example>')
    if 'Date' not in headers or headers['Date'] is not None:
        msg['Date'] = headers.pop('Date', _DATE) or _DATE
    else:
        headers.pop('Date')
    for k, v in headers.items():
        msg[k] = v
    msg.set_content(body)
    return msg.as_bytes()


def test_parse_plain_text_message():
    raw = _message()
    result = parse_message(raw, uid=42)
    assert result['providerMessageId'] == '42'
    assert result['threadId'] is None
    assert result['subject'] == 'Hi'
    assert result['sender'] == 'Acme Recruiting'
    assert result['senderEmail'] == 'recruiting@acme.example'
    assert result['bodyText'] == 'Thanks for applying!'
    assert result['bodyHtml'] == ''
    assert result['labelIds'] == []
    assert result['snippet'] == 'Thanks for applying!'


def test_parse_multipart_prefers_plain_text_over_html():
    msg = EmailMessage()
    msg['Subject'] = 'Hi'
    msg['From'] = 'a@b.com'
    msg['Date'] = _DATE
    msg.set_content('plain body')
    msg.add_alternative('<p>html body</p>', subtype='html')

    result = parse_message(msg.as_bytes(), uid=7)

    assert result['bodyText'] == 'plain body'
    # get_content() appends a trailing newline for text parts.
    assert result['bodyHtml'] == '<p>html body</p>\n'
    # Bare address with no display name: sender name is None, not the raw string.
    assert result['sender'] is None
    assert result['senderEmail'] == 'a@b.com'


def test_parse_html_only_falls_back_to_stripped_text():
    msg = EmailMessage()
    msg['Subject'] = 'Hi'
    msg['Date'] = _DATE
    msg.set_content('<div>Hello <b>world</b></div>', subtype='html')

    result = parse_message(msg.as_bytes(), uid=9)

    assert 'Hello' in result['bodyText']
    assert 'world' in result['bodyText']
    assert '<' not in result['bodyText']
    assert result['bodyHtml'] == '<div>Hello <b>world</b></div>\n'


def test_rfc2047_encoded_subject_is_decoded():
    """email.policy.default auto-decodes RFC2047 encoded-word headers, so a
    non-ASCII subject round-trips through .as_bytes() -> parse_message()
    without any hand-rolled decoding."""
    msg = EmailMessage()
    msg['Subject'] = 'Café ☕ meetup'
    msg['From'] = 'a@b.com'
    msg['Date'] = _DATE
    msg.set_content('body')

    result = parse_message(msg.as_bytes(), uid=1)

    assert result['subject'] == 'Café ☕ meetup'


def test_missing_date_falls_back_to_now():
    raw = _message(Date=None)
    before = int(time.time())
    result = parse_message(raw, uid=2)
    after = int(time.time())
    assert before <= result['receivedAt'] <= after


def test_malformed_date_header_falls_back_to_now_without_raising():
    msg = EmailMessage()
    msg['Subject'] = 'Bad date'
    msg['From'] = 'a@b.com'
    msg['Date'] = 'not a date at all'
    msg.set_content('body')

    before = int(time.time())
    result = parse_message(msg.as_bytes(), uid=3)
    after = int(time.time())

    assert before <= result['receivedAt'] <= after


def test_empty_body_yields_empty_text_and_no_snippet():
    msg = EmailMessage()
    msg['Subject'] = 'Empty'
    msg['From'] = 'a@b.com'
    msg['Date'] = _DATE
    msg.set_content('')

    result = parse_message(msg.as_bytes(), uid=4)

    assert result['bodyText'] == ''
    assert result['snippet'] is None


def test_snippet_is_first_200_chars_of_body_text():
    msg = EmailMessage()
    msg['Subject'] = 'Long'
    msg['From'] = 'a@b.com'
    msg['Date'] = _DATE
    msg.set_content('x' * 300)

    result = parse_message(msg.as_bytes(), uid=5)

    assert result['snippet'] == 'x' * 200


def test_xoauth2_string_exact_byte_format():
    """A one-character mistake here fails silently as an auth rejection, so
    the exact format is pinned rather than loosely asserted."""
    s = _xoauth2_string('me@example.com', 'tok123')
    assert s == 'user=me@example.com\x01auth=Bearer tok123\x01\x01'


def test_connect_requires_exactly_one_of_password_or_access_token():
    with pytest.raises(ValueError):
        imap_client.connect('host', 993, username='u')
    with pytest.raises(ValueError):
        imap_client.connect('host', 993, username='u', password='p', access_token='t')


# --- small IMAP response parsers: STATUS / SEARCH / FETCH ---


class _StubConn:
    """Just enough of imaplib.IMAP4_SSL for the parsers below — no socket."""

    def __init__(self, status=None, search=None, fetch=None):
        self._status = status
        self._search = search
        self._fetch = fetch

    def status(self, folder, names):
        return self._status

    def uid(self, command, *args):
        if command == 'search':
            return self._search
        if command == 'fetch':
            return self._fetch
        raise AssertionError(f'unexpected uid command {command}')


def test_folder_status_parses_uidvalidity_and_uidnext():
    conn = _StubConn(status=('OK', [b'"INBOX" (UIDVALIDITY 100 UIDNEXT 42)']))
    assert imap_client.folder_status(conn) == {'uidValidity': 100, 'uidNext': 42}


def test_folder_status_raises_imap_error_with_servers_text():
    conn = _StubConn(status=('NO', [b'Mailbox does not exist']))
    with pytest.raises(imap_client.ImapError) as excinfo:
        imap_client.folder_status(conn)
    assert 'Mailbox does not exist' in str(excinfo.value)


def test_search_all_uids_parses_space_separated_list():
    conn = _StubConn(search=('OK', [b'1 2 3']))
    assert imap_client.search_all_uids(conn) == [1, 2, 3]


def test_search_all_uids_empty_mailbox_returns_empty_list():
    conn = _StubConn(search=('OK', [b'']))
    assert imap_client.search_all_uids(conn) == []


def test_search_uids_since_filters_the_rfc3501_boundary_replay():
    """RFC 3501: UID n:* with n beyond the highest UID still returns that
    highest UID once. search_uids_since must filter it back out so an
    already-known message never reaches the caller at all."""
    conn = _StubConn(search=('OK', [b'50']))
    assert imap_client.search_uids_since(conn, uid_next=100) == []


def test_search_uids_since_includes_uids_at_or_above_next():
    conn = _StubConn(search=('OK', [b'100 101 102']))
    assert imap_client.search_uids_since(conn, uid_next=100) == [100, 101, 102]


def test_fetch_message_extracts_literal_from_tuple_response():
    raw_bytes = b'Subject: Hi\r\n\r\nBody'
    conn = _StubConn(fetch=('OK', [(b'1 (UID 5 BODY[] {20}', raw_bytes), b')']))
    assert imap_client.fetch_message(conn, 5) == raw_bytes


def test_fetch_message_returns_none_when_message_is_gone():
    """OK status but no literal data: the message was deleted between SEARCH
    and FETCH — routine, not a sync failure."""
    conn = _StubConn(fetch=('OK', [None]))
    assert imap_client.fetch_message(conn, 5) is None


def test_fetch_message_raises_on_no_response():
    conn = _StubConn(fetch=('NO', [b'Some error']))
    with pytest.raises(imap_client.ImapError):
        imap_client.fetch_message(conn, 5)
