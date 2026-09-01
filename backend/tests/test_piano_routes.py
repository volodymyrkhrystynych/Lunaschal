import io
import zipfile


SCORE = b'''<?xml version="1.0"?>
<score-partwise version="4.0">
  <work><work-title>Minuet in G</work-title></work>
  <identification><creator type="composer">Christian Petzold</creator></identification>
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1"><measure number="1"><attributes><divisions>1</divisions></attributes>
    <note><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration></note>
  </measure></part>
</score-partwise>'''


def test_import_list_fetch_and_delete_musicxml(client, tmp_path, monkeypatch):
    monkeypatch.setenv('PIANO_ROOT', str(tmp_path / 'piano'))

    response = client.post(
        '/api/piano/pieces',
        data={'file': (io.BytesIO(SCORE), 'minuet.musicxml')},
    )
    assert response.status_code == 201
    piece = response.get_json()
    assert piece['title'] == 'Minuet in G'
    assert piece['composer'] == 'Christian Petzold'

    assert client.get('/api/piano/pieces').get_json()[0]['id'] == piece['id']
    score = client.get(f"/api/piano/pieces/{piece['id']}/score")
    assert score.status_code == 200
    assert b'<score-partwise' in score.data

    assert client.delete(f"/api/piano/pieces/{piece['id']}").status_code == 200
    assert client.get('/api/piano/pieces').get_json() == []


def test_imports_compressed_mxl(client, tmp_path, monkeypatch):
    monkeypatch.setenv('PIANO_ROOT', str(tmp_path / 'piano'))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr(
            'META-INF/container.xml',
            '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>',
        )
        zf.writestr('score.xml', SCORE)

    response = client.post(
        '/api/piano/pieces',
        data={'file': (io.BytesIO(archive.getvalue()), 'minuet.mxl')},
    )

    assert response.status_code == 201
    assert response.get_json()['sourceFilename'] == 'minuet.mxl'


def test_rejects_arbitrary_xml(client):
    response = client.post(
        '/api/piano/pieces',
        data={'file': (io.BytesIO(b'<html/>'), 'page.xml')},
    )
    assert response.status_code == 400
    assert 'MusicXML' in response.get_json()['error']
