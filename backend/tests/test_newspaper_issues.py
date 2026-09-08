from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from backend.newspapers import issues


@pytest.fixture(autouse=True)
def archive(monkeypatch, tmp_path):
    monkeypatch.setenv('NEWSPAPERS_ARCHIVE_ROOT', str(tmp_path / 'archive'))


def pdf():
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    output.seek(0)
    return output


def upload(client, date='2026-09-01'):
    return client.post(f'/api/newspapers/issues/{date}', data={'file': (pdf(), 'star.pdf')})


def test_archive_immutable_and_range_requests(client):
    response = upload(client)
    assert response.status_code == 201
    assert response.json['pageCount'] == 1
    url = response.json['pdfUrl']
    original = client.get(url).data
    assert original.startswith(b'%PDF-')
    assert client.get(url, headers={'Range': 'bytes=0-9'}).status_code == 206
    assert upload(client).status_code == 409
    assert client.get(url).data == original
    assert client.get('/api/newspapers/issues').json['issues'][0]['date'] == '2026-09-01'


@pytest.mark.parametrize('value', ['2026-99-99', '20260901', 'not-a-date'])
def test_bad_dates(client, value):
    assert upload(client, value).status_code == 400
    assert client.get(f'/api/newspapers/issues/{value}/pdf').status_code == 404


def test_reject_html_and_partial_download(client, monkeypatch):
    response = client.post('/api/newspapers/issues/2026-09-01', data={'file': (BytesIO(b'<html>Login</html>'), 'star.pdf')})
    assert response.status_code == 400
    monkeypatch.setattr(issues, 'MAX_PDF_BYTES', 10)
    assert upload(client).status_code == 400
    assert client.get('/api/newspapers/issues').json['issues'] == []
    assert not list(issues.archive_root().rglob('*.part'))


def test_markup_revision_prevents_lost_updates(client):
    upload(client)
    url = '/api/newspapers/issues/2026-09-01/markup'
    assert client.get(url).json == {'revision': 0, 'strokes': []}
    strokes = [{'page': 1, 'tool': 'highlight', 'points': [[0.1, 0.2], [0.8, 0.2]]}]
    assert client.put(url, json={'revision': 0, 'strokes': strokes}).json == {'revision': 1}
    assert client.put(url, json={'revision': 0, 'strokes': []}).status_code == 409
    assert client.get(url).json == {'revision': 1, 'strokes': strokes}
    assert client.put(url, json={'revision': 1, 'strokes': []}).status_code == 200


@pytest.mark.parametrize('stroke', [
    {'page': 2, 'tool': 'pen', 'points': [[0, 0]]},
    {'page': True, 'tool': 'pen', 'points': [[0, 0]]},
    {'page': 1, 'tool': 'script', 'points': [[0, 0]]},
    {'page': 1, 'tool': 'pen', 'points': [[-1, 0]]},
    {'page': 1, 'tool': 'pen', 'points': [[True, 0]]},
    {'page': 1, 'tool': 'pen', 'points': []},
])
def test_markup_validation(client, stroke):
    upload(client)
    assert client.put('/api/newspapers/issues/2026-09-01/markup', json={'revision': 0, 'strokes': [stroke]}).status_code == 400


def test_missing_archive_keeps_markup(client):
    upload(client)
    issues.issue_path('2026-09-01').unlink()
    assert client.get('/api/newspapers/issues/2026-09-01/pdf').status_code == 404
    assert client.get('/api/newspapers/issues/2026-09-01/markup').status_code == 200


@pytest.mark.parametrize('password', ['', 'requires-password'])
def test_passwordless_encryption_is_normalized_but_passwords_are_rejected(client, password):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password=password, owner_password='publisher')
    stream = BytesIO()
    writer.write(stream)
    stream.seek(0)
    response = client.post('/api/newspapers/issues/2026-09-01', data={'file': (stream, 'issue.pdf')})
    if password:
        assert response.status_code == 400
        assert not issues.issue_path('2026-09-01').exists()
    else:
        assert response.status_code == 201
        stored = PdfReader(issues.issue_path('2026-09-01'))
        assert not stored.is_encrypted
        assert len(stored.pages) == 1
        assert float(stored.pages[0].mediabox.width) == 612
