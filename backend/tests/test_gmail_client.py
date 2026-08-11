"""Network-free tests for backend/email/gmail_client.py: the message parser
and the error surfacing.

Gmail's users.messages.get response is compact enough to build inline as
fixture dicts rather than loading files, so no fixtures/ directory here —
and the error bodies are small enough for the same treatment.
"""
import base64
import json

import pytest
import requests

from backend.email import gmail_client
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


# --- error surfacing: Google's body, not just the status line ---


class _FakeResponse:
    """Enough of requests.Response for _raise_for_status."""

    def __init__(self, status_code, body=None, text=''):
        self.status_code = status_code
        self._body = body
        self.text = text if body is None else json.dumps(body)
        self.url = 'https://gmail.googleapis.com/gmail/v1/users/me/profile'

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


def test_disabled_api_403_keeps_googles_explanation():
    """The failure the user actually hit: consent succeeds, then the first
    API call 403s because the Gmail API was never enabled on the project.
    Google's body names the project and links the enable page; the bare
    status line does not, which is why raise_for_status isn't enough."""
    resp = _FakeResponse(403, {'error': {
        'code': 403,
        'message': (
            'Gmail API has not been used in project 12345 before or it is '
            'disabled. Enable it by visiting https://console.developers.google'
            '.com/apis/api/gmail.googleapis.com/overview?project=12345'
        ),
        'errors': [{'message': 'Access Not Configured.', 'reason': 'accessNotConfigured'}],
        'status': 'PERMISSION_DENIED',
    }})
    with pytest.raises(gmail_client.GmailApiError) as excinfo:
        gmail_client._raise_for_status(resp)
    assert 'has not been used in project 12345' in str(excinfo.value)
    assert excinfo.value.status_code == 403
    assert excinfo.value.reason == 'PERMISSION_DENIED'


def test_insufficient_scope_403_is_distinguishable_from_a_disabled_api():
    """The other 403 — the read permission was declined on the consent
    screen. Same status code as a disabled API, different fix, so the two
    must not render as the same sentence."""
    resp = _FakeResponse(403, {'error': {
        'code': 403,
        'message': 'Request had insufficient authentication scopes.',
        'status': 'PERMISSION_DENIED',
        'errors': [{'message': 'Insufficient Permission', 'reason': 'insufficientPermissions'}],
    }})
    with pytest.raises(gmail_client.GmailApiError) as excinfo:
        gmail_client._raise_for_status(resp)
    assert 'insufficient authentication scopes' in str(excinfo.value)


def test_oauth_token_endpoint_error_shape_is_understood():
    """The token endpoint uses a different shape from the Gmail API: a bare
    code under `error` with the prose in `error_description`."""
    resp = _FakeResponse(400, {
        'error': 'invalid_grant',
        'error_description': 'Token has been expired or revoked.',
    })
    with pytest.raises(gmail_client.GmailApiError) as excinfo:
        gmail_client._raise_for_status(resp)
    assert 'Token has been expired or revoked.' in str(excinfo.value)
    assert excinfo.value.reason == 'invalid_grant'


def test_non_json_error_body_falls_back_to_truncated_text():
    """A proxy or captive portal answers HTML, not JSON. That still has to
    produce a message rather than a parse error, and it lands in
    last_sync_error, so it must not be a whole page."""
    resp = _FakeResponse(502, body=None, text='<html><body>' + 'x' * 500 + '</body></html>')
    with pytest.raises(gmail_client.GmailApiError) as excinfo:
        gmail_client._raise_for_status(resp)
    assert len(str(excinfo.value)) < 400
    assert excinfo.value.reason is None


def test_error_with_no_recognizable_body_still_names_status_and_url():
    resp = _FakeResponse(500, body={'something': 'unexpected'})
    with pytest.raises(gmail_client.GmailApiError) as excinfo:
        gmail_client._raise_for_status(resp)
    assert '500' in str(excinfo.value)
    assert 'users/me/profile' in str(excinfo.value)


def test_success_does_not_raise():
    gmail_client._raise_for_status(_FakeResponse(200, {'emailAddress': 'a@b.example'}))


def test_gmail_api_error_is_caught_as_a_requests_error():
    """sync.py and the scheduler catch broadly, but anything narrowing to
    requests.RequestException must keep working."""
    assert issubclass(gmail_client.GmailApiError, requests.RequestException)
