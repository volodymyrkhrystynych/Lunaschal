"""A to-do that was spoken: `POST /api/tasks/todos/recordings`.

The phone's mic beside "Add to-do" used to wait on two requests and delete the
clip whatever happened. Now it holds the clip until this route has it, and the
server does the rest in the background — the journal/food/chat recording
contract. What is pinned here is that contract: the clip lands, becomes a to-do
under the phone's id, a replay is a no-op, and nothing said is ever lost.
"""
import io
from unittest.mock import patch

import pytest
from ulid import ULID

from backend.ai import todo as todo_ai
from backend.ai.service import Preempted
from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def todos_root(monkeypatch, tmp_path):
    root = tmp_path / 'todos'
    monkeypatch.setenv('TODOS_ROOT', str(root))
    return root


@pytest.fixture
def transcribes(monkeypatch):
    holder = {'text': 'call the dentist tomorrow', 'error': None, 'calls': 0}

    def fake(path, **kwargs):
        holder['calls'] += 1
        if holder['error']:
            raise RuntimeError(holder['error'])
        return holder['text']

    import backend.routes.stt as stt_routes
    monkeypatch.setattr(stt_routes, 'transcribe_file', fake)
    return holder


def _upload(client, rid=None, data=b'\x00' * 2048, mime='audio/mp4', captured_at=None):
    form = {'audio': (io.BytesIO(data), 'recording.m4a', mime), 'id': rid or str(ULID())}
    if captured_at:
        form['capturedAt'] = captured_at
    res = client.post('/api/tasks/todos/recordings', data=form, content_type='multipart/form-data')
    return res, form['id']


def _todo(client, todo_id):
    return next((t for t in client.get('/api/tasks/todos').get_json() if t['id'] == todo_id), None)


FIELDS = {'title': 'Call the dentist', 'notes': None, 'priority': 2, 'due': None}


def test_the_clip_becomes_a_todo_under_the_phones_id(client, transcribes, run_jobs_sync, todos_root):
    with patch('backend.routes.tasks.extract_todo_fields', return_value=FIELDS) as extract:
        res, rid = _upload(client)
    assert res.status_code == 201
    extract.assert_called_once()
    assert extract.call_args.args[0] == 'call the dentist tomorrow'
    todo = _todo(client, rid)
    assert (todo['title'], todo['priority']) == ('Call the dentist', 2)
    row = get_db().execute('SELECT * FROM todo_recordings WHERE id=?', (rid,)).fetchone()
    assert (row['status'], row['transcript'], row['path']) == ('done', 'call the dentist tomorrow', None)
    # The clip is gone once it is a to-do; nothing can play it back.
    assert not (todos_root / rid).exists()
    assert client.get('/api/tasks/todos/recordings').get_json() == []


def test_upload_returns_before_the_work_is_done(client, queued_jobs, todos_root):
    res, rid = _upload(client)
    assert res.status_code == 201
    assert res.get_json()['status'] == 'pending'
    assert (todos_root / rid / 'clip.m4a').read_bytes() == b'\x00' * 2048
    assert _todo(client, rid) is None
    pending = client.get('/api/tasks/todos/recordings').get_json()
    assert [(p['id'], p['status']) for p in pending] == [(rid, 'pending')]
    assert 'path' not in pending[0]


def test_a_replay_is_a_noop_before_and_after_the_todo_exists(client, transcribes, run_jobs_sync):
    with patch('backend.routes.tasks.extract_todo_fields', return_value=FIELDS):
        first, rid = _upload(client)
        again, _ = _upload(client, rid=rid)
    assert (first.status_code, again.status_code) == (201, 200)
    assert transcribes['calls'] == 1
    assert [t['id'] for t in client.get('/api/tasks/todos').get_json()].count(rid) == 1


def test_a_replay_of_a_todo_made_by_the_old_route_is_a_noop(client, transcribes, run_jobs_sync):
    rid = str(ULID())
    with patch('backend.routes.tasks.extract_todo_fields', return_value=None):
        client.post('/api/tasks/todos/transcribe', json={'text': 'water plants', 'id': rid})
    res, _ = _upload(client, rid=rid)
    assert res.status_code == 200
    assert transcribes['calls'] == 0


def test_model_unavailable_still_makes_the_todo_from_the_words(client, transcribes, run_jobs_sync):
    with patch('backend.routes.tasks.extract_todo_fields', return_value=None):
        _, rid = _upload(client)
    assert _todo(client, rid)['title'] == 'call the dentist tomorrow'


def test_failed_transcription_keeps_the_clip_and_says_so(client, transcribes, run_jobs_sync, todos_root):
    transcribes['error'] = 'whisper fell over'
    _, rid = _upload(client)
    assert _todo(client, rid) is None
    [failed] = client.get('/api/tasks/todos/recordings').get_json()
    assert (failed['id'], failed['status'], failed['error']) == (rid, 'error', 'whisper fell over')
    assert (todos_root / rid / 'clip.m4a').exists()
    # Dismissing it removes the clip too.
    assert client.delete(f'/api/tasks/todos/recordings/{rid}').status_code == 200
    assert not (todos_root / rid).exists()
    assert client.get('/api/tasks/todos/recordings').get_json() == []


def test_silence_is_an_error_not_an_empty_todo(client, transcribes, run_jobs_sync):
    transcribes['text'] = '   '
    _, rid = _upload(client)
    [failed] = client.get('/api/tasks/todos/recordings').get_json()
    assert failed['error'] == 'No speech was heard in that recording.'
    assert _todo(client, rid) is None


def test_preempted_requeues_without_transcribing_twice(client, transcribes, run_jobs_sync):
    with patch.object(todo_ai, 'chat_json', side_effect=Preempted()):
        _, rid = _upload(client)
    row = get_db().execute('SELECT * FROM todo_recordings WHERE id=?', (rid,)).fetchone()
    assert (row['status'], row['transcript']) == ('pending', 'call the dentist tomorrow')
    job = get_db().execute("SELECT * FROM llm_jobs WHERE kind='todo.transcribe_recording'").fetchone()
    assert job['status'] == 'pending'

    with patch('backend.routes.tasks.extract_todo_fields', return_value=FIELDS):
        run_jobs_sync.process_one(job['id'])
    assert _todo(client, rid)['title'] == 'Call the dentist'
    assert transcribes['calls'] == 1


def test_a_pending_recording_cannot_be_dismissed(client, queued_jobs):
    _, rid = _upload(client)
    assert client.delete(f'/api/tasks/todos/recordings/{rid}').status_code == 409
    assert client.delete('/api/tasks/todos/recordings/nope').status_code == 404


@pytest.mark.parametrize('form, status', [
    ({'id': 'x'}, 400),  # no audio
    ({'audio': (io.BytesIO(b'\x00' * 10), 'r.m4a', 'audio/mp4')}, 400),  # no id
    ({'audio': (io.BytesIO(b'\x00' * 10), 'r.png', 'image/png'), 'id': 'x'}, 400),
    ({'audio': (io.BytesIO(b''), 'r.m4a', 'audio/mp4'), 'id': 'x'}, 400),
])
def test_rejects_what_cannot_be_a_clip(client, queued_jobs, form, status):
    res = client.post('/api/tasks/todos/recordings', data=form, content_type='multipart/form-data')
    assert res.status_code == status
    assert get_db().execute('SELECT COUNT(*) FROM todo_recordings').fetchone()[0] == 0


def test_capture_time_is_kept(client, queued_jobs):
    res, _ = _upload(client, captured_at='2026-11-09T08:00:00Z')
    assert res.get_json()['capturedAt'].startswith('2026-11-09T08:00:00')
