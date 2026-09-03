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


def test_daily_routine_is_stable_and_records_attempt(client, monkeypatch):
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-02')

    first = client.get('/api/piano/today')
    assert first.status_code == 200
    routine = first.get_json()
    assert routine['dayKey'] == '2026-09-02'
    assert routine['preferences']['sessionMinutes'] == 25
    assert routine['preferences']['jazzPercent'] == 50
    assert {item['style'] for item in routine['exercises']} >= {'shared', 'classical', 'jazz'}
    assert sum(item['minutes'] for item in routine['exercises']) == 25

    again = client.get('/api/piano/today').get_json()
    assert [item['id'] for item in again['exercises']] == [
        item['id'] for item in routine['exercises']
    ]

    gradeable = next(item for item in routine['exercises'] if item['gradeable'])
    score = client.get(f"/api/piano/daily/{gradeable['id']}/score")
    assert score.status_code == 200
    assert b'<score-partwise' in score.data

    attempt = client.post(
        f"/api/piano/daily/{gradeable['id']}/attempts",
        json={'tempo': 80, 'correctNotes': 9, 'wrongNotes': 1},
    )
    assert attempt.status_code == 201
    assert attempt.get_json()['wrongNotes'] == 1
    completed = client.get('/api/piano/today').get_json()
    saved = next(item for item in completed['exercises'] if item['id'] == gradeable['id'])
    assert saved['completedAt'] is not None
    assert saved['latestAttempt']['tempo'] == 80
    history = client.get('/api/piano/history').get_json()
    assert history[0] == {
        'dayKey': '2026-09-02',
        'exerciseCount': len(routine['exercises']),
        'completedCount': 1,
        'minutesPlanned': 25,
        'onsetAccuracy': None,
        'tempoStability': None,
        'velocityEvenness': None,
    }


def test_records_stage_two_metrics_and_rejects_invalid_scores(client, monkeypatch):
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-02')
    daily_id = client.get('/api/piano/today').get_json()['exercises'][0]['id']
    response = client.post(
        f'/api/piano/daily/{daily_id}/attempts',
        json={
            'tempo': 76, 'achievedTempo': 74.5, 'onsetAccuracy': 88,
            'durationAccuracy': 81, 'tempoStability': 92,
            'velocityEvenness': 79,
        },
    )
    assert response.status_code == 201
    assert response.get_json()['achievedTempo'] == 74.5
    history = client.get('/api/piano/history').get_json()[0]
    assert history['onsetAccuracy'] == 88
    assert client.post(
        f'/api/piano/daily/{daily_id}/attempts',
        json={'onsetAccuracy': 101},
    ).status_code == 400


def test_next_day_prioritizes_an_unpracticed_key(client, monkeypatch):
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-02')
    first = client.get('/api/piano/today').get_json()
    first_key = first['exercises'][0]['keyName']
    for item in first['exercises']:
        if item['gradeable']:
            client.post(f"/api/piano/daily/{item['id']}/attempts", json={
                'onsetAccuracy': 95, 'achievedTempo': item['targetTempo']
            })
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-03')
    second = client.get('/api/piano/today').get_json()
    assert second['exercises'][0]['keyName'] != first_key


def test_daily_preferences_apply_to_the_next_routine(client, monkeypatch):
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-02')
    client.get('/api/piano/today')
    response = client.patch(
        '/api/piano/preferences',
        json={'sessionMinutes': 30, 'skillLevel': 'advanced', 'jazzPercent': 100},
    )
    assert response.status_code == 200
    assert response.get_json()['skillLevel'] == 'advanced'

    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-03')
    next_day = client.get('/api/piano/today').get_json()
    assert sum(item['minutes'] for item in next_day['exercises']) == 30
    assert all(item['style'] != 'classical' for item in next_day['exercises'])
    assert next(item for item in next_day['exercises'] if item['gradeable'])['targetTempo'] == 100
    ear = next(item for item in next_day['exercises'] if item['exerciseKey'] == 'ear-phrase')
    ear_score = client.get(f"/api/piano/daily/{ear['id']}/score")
    assert ear_score.status_code == 200
    assert ear_score.data.count(b'<note>') == 6


def test_rejects_invalid_piano_attempt_rating(client, monkeypatch):
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-02')
    daily_id = client.get('/api/piano/today').get_json()['exercises'][0]['id']
    response = client.post(
        f'/api/piano/daily/{daily_id}/attempts', json={'selfRating': 6}
    )
    assert response.status_code == 400


def test_daily_routine_includes_recent_repertoire_within_budget(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv('PIANO_ROOT', str(tmp_path / 'piano'))
    monkeypatch.setattr('backend.piano.daily.day_key_for', lambda: '2026-09-02')
    client.post(
        '/api/piano/pieces',
        data={'file': (io.BytesIO(SCORE), 'minuet.musicxml')},
    )

    routine = client.get('/api/piano/today').get_json()
    repertoire = next(
        item for item in routine['exercises'] if item['exerciseKey'] == 'repertoire'
    )
    assert repertoire['pieceTitle'] == 'Minuet in G'
    assert repertoire['measureStart'] == 1
    assert sum(item['minutes'] for item in routine['exercises']) == 25
