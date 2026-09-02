import io
from pathlib import Path

import pytest

from backend.db.connection import get_db


SCORE = b'''<?xml version="1.0"?>
<score-partwise version="4.0">
  <work><work-title>Invention No. 1</work-title></work>
  <identification><creator type="composer">J. S. Bach</creator></identification>
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1"><measure number="1"><attributes><divisions>1</divisions></attributes>
    <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note>
  </measure></part>
</score-partwise>'''


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    root = tmp_path / 'external' / 'piano-archive'
    root.parent.mkdir()
    monkeypatch.setenv('PIANO_ARCHIVE_ROOT', str(root))
    monkeypatch.setenv('PIANO_ROOT', str(tmp_path / 'local-piano'))
    return root


def test_status_uses_external_archive_override_without_creating_it(client, archive_root):
    response = client.get('/api/piano/archive/status')

    assert response.status_code == 200
    body = response.get_json()
    assert body['root'] == str(archive_root)
    assert body['available'] is True
    assert body['writable'] is True
    assert body['itemCount'] == 0
    assert not archive_root.exists()


def test_status_derives_archive_from_main_backup_destination(client, tmp_path, monkeypatch):
    monkeypatch.delenv('PIANO_ARCHIVE_ROOT', raising=False)
    destination = tmp_path / 'main-backup'
    destination.mkdir()
    get_db().execute(
        'UPDATE settings SET backup_path=?', (str(destination),)
    )
    get_db().commit()

    body = client.get('/api/piano/archive/status').get_json()

    assert body['destination'] == str(destination)
    assert body['root'] == str(destination / 'archive' / 'piano')
    assert body['available'] is True


def test_upload_streams_file_to_archive_and_serves_it(client, archive_root):
    response = client.post(
        '/api/piano/archive/items',
        data={'file': (io.BytesIO(b'%PDF-1.4\nexercise'), 'Hanon exercises.pdf')},
    )

    assert response.status_code == 201
    item = response.get_json()
    assert item['mediaType'] == 'document'
    assert item['favorite'] == 0
    assert item['available'] is True
    assert item['sizeBytes'] == len(b'%PDF-1.4\nexercise')
    assert item['relativePath'].startswith(f"managed/{item['id']}/")
    assert (archive_root / item['relativePath']).read_bytes().endswith(b'exercise')

    listing = client.get('/api/piano/archive/items').get_json()
    assert listing['total'] == 1
    assert listing['items'][0]['title'] == 'Hanon exercises'

    download = client.get(item['fileUrl'])
    assert download.status_code == 200
    assert download.data.endswith(b'exercise')
    assert 'attachment' in download.headers['Content-Disposition']


def test_scan_indexes_existing_tree_incrementally(client, archive_root):
    scores = archive_root / 'downloads' / 'bach'
    scores.mkdir(parents=True)
    (scores / 'invention_1.musicxml').write_bytes(SCORE)
    (archive_root / 'collection.zip').write_bytes(b'not actually a zip')
    hidden = archive_root / '.partial'
    hidden.write_bytes(b'ignore me')

    first = client.post('/api/piano/archive/scan')
    second = client.post('/api/piano/archive/scan')

    assert first.status_code == 200
    assert first.get_json() == {'indexed': 2, 'skipped': 1, 'updated': 0}
    assert second.get_json() == {'indexed': 0, 'skipped': 1, 'updated': 0}
    listing = client.get('/api/piano/archive/items?limit=1').get_json()
    assert listing['total'] == 2
    assert listing['limit'] == 1
    assert len(listing['items']) == 1


def test_favorite_score_promotes_local_copy_and_unfavorite_removes_it(
    client, archive_root
):
    archive_root.mkdir()
    source = archive_root / 'bach_invention.musicxml'
    source.write_bytes(SCORE)
    client.post('/api/piano/archive/scan')
    item = client.get('/api/piano/archive/items').get_json()['items'][0]

    favored = client.patch(
        f"/api/piano/archive/items/{item['id']}", json={'favorite': True}
    )

    assert favored.status_code == 200
    favorite = favored.get_json()
    assert favorite['favorite'] == 1
    assert favorite['title'] == 'Invention No. 1'
    assert favorite['creator'] == 'J. S. Bach'
    assert favorite['pianoPieceId']
    pieces = client.get('/api/piano/pieces').get_json()
    assert [piece['title'] for piece in pieces] == ['Invention No. 1']

    unfavored = client.patch(
        f"/api/piano/archive/items/{item['id']}", json={'favorite': False}
    )

    assert unfavored.status_code == 200
    assert unfavored.get_json()['favorite'] == 0
    assert unfavored.get_json()['pianoPieceId'] is None
    assert client.get('/api/piano/pieces').get_json() == []
    assert source.is_file(), 'unfavoriting must never delete the external archive'


def test_non_score_favorite_stays_in_catalog_without_practice_copy(client, archive_root):
    response = client.post(
        '/api/piano/archive/items',
        data={'file': (io.BytesIO(b'MThd'), 'performance.mid')},
    )
    item = response.get_json()

    favored = client.patch(
        f"/api/piano/archive/items/{item['id']}", json={'favorite': True}
    )

    assert favored.status_code == 200
    assert favored.get_json()['favorite'] == 1
    assert favored.get_json()['pianoPieceId'] is None
    assert client.get('/api/piano/pieces').get_json() == []


def test_broken_musicxml_is_archived_but_cannot_enter_practice_library(
    client, archive_root
):
    response = client.post(
        '/api/piano/archive/items',
        data={'file': (io.BytesIO(b'<html/>'), 'broken.musicxml')},
    )
    item = response.get_json()

    assert item['practiceCompatible'] == 0
    # It can still be favorited as an archive record; incompatible media is not
    # silently presented as a playable score.
    favored = client.patch(
        f"/api/piano/archive/items/{item['id']}", json={'favorite': True}
    )
    assert favored.status_code == 200
    assert favored.get_json()['pianoPieceId'] is None


def test_scanned_broken_musicxml_becomes_archive_only_favorite(client, archive_root):
    archive_root.mkdir()
    (archive_root / 'broken.musicxml').write_bytes(b'<html/>')
    client.post('/api/piano/archive/scan')
    item = client.get('/api/piano/archive/items').get_json()['items'][0]
    assert item['practiceCompatible'] == 1, 'scan only classifies by extension'

    response = client.patch(
        f"/api/piano/archive/items/{item['id']}", json={'favorite': True}
    )

    assert response.status_code == 200
    favorite = response.get_json()
    assert favorite['favorite'] == 1
    assert favorite['practiceCompatible'] == 0
    assert favorite['pianoPieceId'] is None
    assert client.get('/api/piano/pieces').get_json() == []


def test_tampered_relative_path_cannot_escape_archive(client, archive_root, tmp_path):
    response = client.post(
        '/api/piano/archive/items',
        data={'file': (io.BytesIO(b'MThd'), 'safe.mid')},
    )
    item = response.get_json()
    outside = tmp_path / 'outside.mid'
    outside.write_bytes(b'secret')
    get_db().execute(
        'UPDATE media_archive_items SET relative_path=? WHERE id=?',
        ('../../outside.mid', item['id']),
    )
    get_db().commit()

    response = client.get(item['fileUrl'])

    assert response.status_code == 404
    assert response.get_json()['error'] == 'The archived file is missing.'


def test_archive_write_reports_disconnected_drive(client, tmp_path, monkeypatch):
    monkeypatch.delenv('PIANO_ARCHIVE_ROOT', raising=False)
    missing = tmp_path / 'unplugged' / 'lunaschal'
    get_db().execute('UPDATE settings SET backup_path=?', (str(missing),))
    get_db().commit()

    response = client.post('/api/piano/archive/scan')

    assert response.status_code == 400
    assert response.get_json()['error'] == 'The backup drive is not connected.'
    assert not missing.exists()
