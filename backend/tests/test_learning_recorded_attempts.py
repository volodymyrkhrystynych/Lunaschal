"""A review answer that was spoken: `POST /api/learning/attempts/recording`.

The phone moves on to the next card the moment recording stops, so the clip is
the answer: stored, then transcribed and graded by the attempt's grading job.
What is pinned here is that nothing said is lost on the way — the transcript
becomes the attempt's answer, a replay stores nothing twice, and the clip is
kept until its words are saved.
"""
import io

import pytest

from backend.ai import learning_generation, learning_grading
from backend.db.connection import get_db
from backend.learning import storage
from backend.routes import learning as learning_routes


@pytest.fixture(autouse=True)
def learning_root(monkeypatch, tmp_path):
    root = tmp_path / 'learning'
    monkeypatch.setenv('LEARNING_ROOT', str(root))
    return root


@pytest.fixture(autouse=True)
def inline_bg(run_jobs_sync):
    learning_routes._grading_queued.clear()
    yield
    learning_routes._grading_queued.clear()


@pytest.fixture
def heard(monkeypatch):
    holder = {'text': 'x is a thing', 'error': None, 'calls': 0}

    def fake(path, **kwargs):
        holder['calls'] += 1
        if holder['error']:
            raise RuntimeError(holder['error'])
        return holder['text']

    import backend.routes.stt as stt_routes
    monkeypatch.setattr(stt_routes, 'transcribe_file', fake)
    return holder


@pytest.fixture(autouse=True)
def stub_grader(monkeypatch):
    graded = []
    monkeypatch.setattr(learning_grading, 'decompose_claims',
                        lambda q, a: [{'text': 'X is a thing', 'essential': True}])

    def coverage(claims, answer, speech=False):
        graded.append(answer)
        return {'claims': [{**c, 'covered': True, 'note': ''} for c in claims], 'summary': 'ok'}

    monkeypatch.setattr(learning_grading, 'check_coverage', coverage)
    monkeypatch.setattr(learning_generation, 'normalize_transcript', lambda t: t)
    return graded


def _card(client):
    return client.post('/api/learning/cards', json={'question': 'What is X?', 'answer': 'X is a thing.'}).json['id']


def _send(client, card_id, id='01JATTEMPT0000000000000001', audio=b'm4a bytes', **form):
    return client.post('/api/learning/attempts/recording', data={
        'id': id, 'cardId': card_id,
        'audio': (io.BytesIO(audio), 'answer.m4a', 'audio/mp4'), **form,
    }, content_type='multipart/form-data')


def _attempt(client, card_id):
    return next(a for a in client.get('/api/learning/attempts').json if a['cardId'] == card_id)


def test_the_clip_is_transcribed_into_the_answer_and_graded(client, heard, stub_grader, learning_root):
    cid = _card(client)
    r = _send(client, cid)
    assert r.status_code == 200

    attempt = _attempt(client, cid)
    assert attempt['answer'] == 'x is a thing'
    assert attempt['answerMode'] == 'voice'
    assert attempt['gradeStatus'] == 'done'
    assert stub_grader == ['x is a thing']
    # The words are saved, so the audio is gone.
    assert not (learning_root / '01JATTEMPT0000000000000001').exists()


def test_typed_text_comes_before_the_transcript(client, heard):
    cid = _card(client)
    _send(client, cid, answer='Well,')
    assert _attempt(client, cid)['answer'] == 'Well, x is a thing'


def test_speech_mode_is_stamped(client, heard):
    cid = _card(client)
    _send(client, cid, speechMode='true')
    row = get_db().execute('SELECT speech_requested FROM learning_attempts WHERE card_id=?', (cid,)).fetchone()
    assert row['speech_requested'] == 1


def test_a_replay_is_a_noop(client, heard):
    cid = _card(client)
    _send(client, cid)
    r = _send(client, cid)
    assert r.status_code == 200
    assert heard['calls'] == 1
    assert len(client.get('/api/learning/attempts').json) == 1


def test_a_failed_transcription_keeps_the_clip_until_the_card_is_rated(client, heard, learning_root):
    heard['error'] = 'No speech found in the recording'
    cid = _card(client)
    _send(client, cid)

    attempt = _attempt(client, cid)
    assert attempt['gradeStatus'] == 'error'
    assert attempt['answer'] is None
    assert storage.find_clip('01JATTEMPT0000000000000001') is not None

    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 1})
    assert not (learning_root / '01JATTEMPT0000000000000001').exists()


def test_rating_before_transcription_removes_the_clip(client, monkeypatch, heard, learning_root):
    from backend.ai import jobs
    monkeypatch.setattr(jobs, 'enqueue', lambda *a, **k: None)
    cid = _card(client)
    _send(client, cid)
    assert storage.find_clip('01JATTEMPT0000000000000001') is not None

    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 3})
    assert not (learning_root / '01JATTEMPT0000000000000001').exists()
    assert heard['calls'] == 0


def test_a_spoken_attempt_with_no_clip_and_no_words_is_an_error_not_pending(client, monkeypatch, heard):
    from backend.ai import jobs
    from backend.learning.attempts import grade_attempt
    monkeypatch.setattr(jobs, 'enqueue', lambda *a, **k: None)
    cid = _card(client)
    _send(client, cid)
    storage.delete_attempt_dir('01JATTEMPT0000000000000001')

    grade_attempt('01JATTEMPT0000000000000001')
    assert _attempt(client, cid)['gradeStatus'] == 'error'


def test_re_answering_by_typing_drops_the_waiting_clip(client, monkeypatch, heard, learning_root):
    from backend.ai import jobs
    monkeypatch.setattr(jobs, 'enqueue', lambda *a, **k: None)
    cid = _card(client)
    _send(client, cid)
    client.post('/api/learning/attempts', json={'cardId': cid, 'mode': 'answered', 'answer': 'typed'})
    assert not (learning_root / '01JATTEMPT0000000000000001').exists()


@pytest.mark.parametrize('audio, form, status', [
    (b'', {}, 400),
])
def test_an_empty_recording_is_refused_and_nothing_is_stored(client, heard, learning_root, audio, form, status):
    cid = _card(client)
    assert _send(client, cid, audio=audio, **form).status_code == status
    assert client.get('/api/learning/attempts').json == []
    assert not (learning_root / '01JATTEMPT0000000000000001').exists()


def test_a_non_audio_upload_is_refused(client, heard):
    cid = _card(client)
    r = client.post('/api/learning/attempts/recording', data={
        'id': '01JATTEMPT0000000000000001', 'cardId': cid,
        'audio': (io.BytesIO(b'png'), 'page.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400


def test_an_unknown_card_is_404(client, heard):
    assert _send(client, 'nope').status_code == 404


# ---- retiring a card mid-review

def test_retiring_takes_the_card_out_of_review_and_closes_its_attempt(client, monkeypatch, heard, learning_root):
    from backend.ai import jobs
    monkeypatch.setattr(jobs, 'enqueue', lambda *a, **k: None)
    cid = _card(client)
    _send(client, cid)

    assert client.post(f'/api/learning/cards/{cid}/retire').status_code == 200
    assert client.get('/api/learning/due').json == []
    assert client.get('/api/learning/attempts').json == []
    assert client.get('/api/learning/stats').json['total'] == 0
    assert not (learning_root / '01JATTEMPT0000000000000001').exists()
    # Kept, not deleted.
    assert client.get(f'/api/learning/cards/{cid}').json['state'] == 'retired'


def test_only_an_active_card_can_be_retired(client):
    cid = _card(client)
    client.post(f'/api/learning/cards/{cid}/retire')
    assert client.post(f'/api/learning/cards/{cid}/retire').status_code == 400
    assert client.post('/api/learning/cards/nope/retire').status_code == 404
