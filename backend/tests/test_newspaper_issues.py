import json
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from backend.newspapers import issues


@pytest.fixture(autouse=True)
def archive(monkeypatch, tmp_path):
    monkeypatch.setenv('NEWSPAPERS_ARCHIVE_ROOT', str(tmp_path / 'archive'))


def pdf(pages=1):
    output = BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    writer.write(output)
    output.seek(0)
    return output


def upload(client, date='2026-09-01', pages=1):
    return client.post(f'/api/newspapers/issues/{date}', data={'file': (pdf(pages), 'star.pdf')})


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
    # Pressure rides along as a third coordinate, and is a 0..1 value like the
    # other two. A fourth number means the client and the server disagree about
    # what a point is, which is not something to guess at.
    {'page': 1, 'tool': 'pen', 'points': [[0, 0, 2]]},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0, True]]},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0, 0, 0]]},
    # A width has to be a positive, bounded, real number.
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'size': 0},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'size': -1},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'size': True},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'size': 'big'},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'size': 5000},
    # A colour is echoed back to a reader and painted, so it is a hex triple or
    # it is nothing.
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'color': 'red'},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'color': '#ff'},
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'color': 1},
    # Unknown keys stay refused: the blob is handed back to a client, so it
    # must not become a place to park arbitrary data.
    {'page': 1, 'tool': 'pen', 'points': [[0, 0]], 'colour': '#ff0000'},
])
def test_markup_validation(client, stroke):
    upload(client)
    assert client.put('/api/newspapers/issues/2026-09-01/markup', json={'revision': 0, 'strokes': [stroke]}).status_code == 400


def test_a_stroke_keeps_its_pressure_width_and_colour(client):
    upload(client)
    url = '/api/newspapers/issues/2026-09-01/markup'
    stroke = {'page': 1, 'tool': 'pen', 'points': [[0.1, 0.2, 0.4], [0.8, 0.2, 1]],
              'size': 3, 'color': '#c0392b'}
    assert client.put(url, json={'revision': 0, 'strokes': [stroke]}).json == {'revision': 1}
    assert client.get(url).json['strokes'] == [stroke]


def test_the_two_names_for_the_highlighter_are_stored_as_one(client):
    # The client shared ink model calls it 'highlighter'; the column has always
    # held 'highlight'. Both are accepted so a version skew cannot fail a save,
    # and one is stored so the column cannot go bimodal.
    upload(client)
    url = '/api/newspapers/issues/2026-09-01/markup'
    client.put(url, json={'revision': 0, 'strokes': [
        {'page': 1, 'tool': 'highlighter', 'points': [[0, 0]]},
        {'page': 1, 'tool': 'highlight', 'points': [[1, 1]]},
    ]})
    assert [s['tool'] for s in client.get(url).json['strokes']] == ['highlight', 'highlight']


def test_markup_written_before_any_of_this_still_saves(client):
    # The shape every existing issue is stored in: no size, no colour, two
    # numbers per point. It has to keep validating exactly as it did.
    upload(client)
    url = '/api/newspapers/issues/2026-09-01/markup'
    old = [{'page': 1, 'tool': 'pen', 'points': [[0.1, 0.2], [0.8, 0.2]]}]
    assert client.put(url, json={'revision': 0, 'strokes': old}).status_code == 200
    assert client.get(url).json['strokes'] == old
    assert issues.marked_pages(json.dumps(old)) == {1}


def test_marked_pages_counts_the_new_shape_too():
    strokes = [{'page': 4, 'tool': 'highlight', 'points': [[0, 0, 0.5]], 'size': 16, 'color': '#ffdb00'}]
    assert issues.marked_pages(json.dumps(strokes)) == {4}


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


def test_journal_feed_counts_marked_pages_for_every_archived_issue(client):
    """The Journal card carries an issue whether it was written on or not, and
    its stat counts pages touched, not strokes drawn."""
    assert upload(client, '2026-09-01', pages=4).status_code == 201
    assert upload(client, '2026-09-02', pages=3).status_code == 201
    strokes = [
        {'page': 1, 'tool': 'pen', 'points': [[0.1, 0.2], [0.3, 0.4]]},
        {'page': 1, 'tool': 'highlight', 'points': [[0.5, 0.5], [0.6, 0.5]]},
        {'page': 3, 'tool': 'pen', 'points': [[0.2, 0.2]]},
    ]
    assert client.put('/api/newspapers/issues/2026-09-01/markup',
                      json={'revision': 0, 'strokes': strokes}).status_code == 200
    feed = client.get('/api/newspapers/issues/journal').json
    assert [(item['date'], item['markedPages'], item['pageCount']) for item in feed] == [
        ('2026-09-02', 0, 3),
        ('2026-09-01', 2, 4),
    ]
    assert feed[1]['pdfUrl'] == '/api/newspapers/issues/2026-09-01/pdf'
    assert feed[0]['archivedAt'].startswith('20')


def test_journal_feed_is_empty_before_anything_is_archived(client):
    assert client.get('/api/newspapers/issues/journal').json == []


@pytest.mark.parametrize('markup', ['', 'not json', '{"page": 1}', '[{"tool": "pen"}]', '[3]'])
def test_marked_pages_survives_unreadable_markup(markup):
    """The count is derived on read, so it must never be the thing that 500s the
    journal feed — every row predating the column defaults to '[]'."""
    assert issues.marked_pages(markup) == set()
