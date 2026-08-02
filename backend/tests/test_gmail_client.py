"""Pure parser tests for backend/email/gmail_client.py::parse_message.

Gmail's users.messages.get response is compact enough to build inline as
fixture dicts rather than loading files, so no fixtures/ directory here.
"""
import base64

from backend.email.gmail_client import parse_message


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_parse_plain_text_message():
    raw = {
        'id': 'abc123',
        'threadId': 'thread456',
        'labelIds': ['INBOX', 'UNREAD'],
        'snippet': 'Thanks for applying...',
        'internalDate': '1700000000000',
        'payload': {
            'headers': [
                {'name': 'Subject', 'value': 'Your application to Acme Corp'},
                {'name': 'From', 'value': 'Acme Recruiting <recruiting@acme.example>'},
            ],
            'mimeType': 'text/plain',
            'body': {'data': _b64url('Thanks for applying to Acme Corp!')},
        },
    }
    result = parse_message(raw)
    assert result == {
        'gmailId': 'abc123',
        'threadId': 'thread456',
        'subject': 'Your application to Acme Corp',
        'sender': 'Acme Recruiting',
        'senderEmail': 'recruiting@acme.example',
        'snippet': 'Thanks for applying...',
        'bodyText': 'Thanks for applying to Acme Corp!',
        'labelIds': ['INBOX', 'UNREAD'],
        'receivedAt': 1700000000,
    }


def test_parse_multipart_prefers_plain_text_over_html():
    raw = {
        'id': 'm1', 'threadId': 't1', 'labelIds': [], 'snippet': '',
        'internalDate': '1700000000000',
        'payload': {
            'headers': [{'name': 'Subject', 'value': 'Hi'}, {'name': 'From', 'value': 'a@b.com'}],
            'mimeType': 'multipart/alternative',
            'parts': [
                {'mimeType': 'text/plain', 'body': {'data': _b64url('plain body')}},
                {'mimeType': 'text/html', 'body': {'data': _b64url('<p>html body</p>')}},
            ],
        },
    }
    result = parse_message(raw)
    assert result['bodyText'] == 'plain body'
    # Bare address with no display name: sender name is None, not the raw string.
    assert result['sender'] is None
    assert result['senderEmail'] == 'a@b.com'


def test_parse_html_only_falls_back_to_stripped_text():
    raw = {
        'id': 'm2', 'threadId': 't2', 'labelIds': [], 'snippet': '',
        'internalDate': '1700000000000',
        'payload': {
            'headers': [],
            'mimeType': 'text/html',
            'body': {'data': _b64url('<div>Hello <b>world</b></div>')},
        },
    }
    result = parse_message(raw)
    assert 'Hello' in result['bodyText']
    assert 'world' in result['bodyText']
    assert '<' not in result['bodyText']


def test_parse_nested_multipart_mixed_with_attachment_part_ignored():
    raw = {
        'id': 'm3', 'threadId': 't3', 'labelIds': [], 'snippet': '',
        'internalDate': '1700000000000',
        'payload': {
            'headers': [],
            'mimeType': 'multipart/mixed',
            'parts': [
                {
                    'mimeType': 'multipart/alternative',
                    'parts': [
                        {'mimeType': 'text/plain', 'body': {'data': _b64url('body text')}},
                    ],
                },
                {'mimeType': 'application/pdf', 'body': {'attachmentId': 'xyz', 'size': 1024}},
            ],
        },
    }
    result = parse_message(raw)
    assert result['bodyText'] == 'body text'


def test_parse_missing_body_yields_empty_text():
    raw = {'id': 'm4', 'threadId': 't4', 'labelIds': [], 'snippet': '', 'payload': {'headers': []}}
    result = parse_message(raw)
    assert result['bodyText'] == ''
    assert result['subject'] is None
