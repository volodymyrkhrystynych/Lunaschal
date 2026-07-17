"""Route tests for /api/meetings with ffmpeg and the pipeline mocked out."""
import io
import subprocess
import time

import pytest

from backend.meetings import recorder, storage


class FakePopen:
    def __init__(self):
        self.returncode = None
        self.signals = []
        self.stdin = None

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            raise RuntimeError('would block')
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


@pytest.fixture
def rec(monkeypatch, tmp_path):
    """Isolate recorder state and stub out pactl/ffmpeg/pipeline/sleep."""
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    monkeypatch.setattr(recorder, '_active', None)
    monkeypatch.setattr(recorder, '_default_sink', lambda: 'fake_sink')
    spawned = []

    def fake_spawn(pulse_input, out_path):
        p = FakePopen()
        spawned.append((pulse_input, str(out_path), p))
        return p

    monkeypatch.setattr(recorder, '_spawn_ffmpeg', fake_spawn)
    monkeypatch.setattr(time, 'sleep', lambda s: None)

    pipeline_calls = []
    monkeypatch.setattr('backend.routes.meetings.start_pipeline', pipeline_calls.append)
    return {'spawned': spawned, 'pipeline_calls': pipeline_calls}


def _start(client):
    resp = client.post('/api/meetings/start')
    assert resp.status_code == 201
    return resp.get_json()['id']


def test_start_creates_row_and_spawns_both_tracks(client, rec):
    meeting_id = _start(client)
    inputs = [s[0] for s in rec['spawned']]
    assert inputs == ['default', 'fake_sink.monitor']
    listing = client.get('/api/meetings').get_json()
    assert len(listing) == 1
    assert listing[0]['id'] == meeting_id
    assert listing[0]['status'] == 'recording'


def test_second_start_conflicts(client, rec):
    _start(client)
    resp = client.post('/api/meetings/start')
    assert resp.status_code == 409
    assert len(client.get('/api/meetings').get_json()) == 1


def test_failed_spawn_rolls_back_row(client, rec, monkeypatch):
    def boom(pulse_input, out_path):
        raise RuntimeError('no such device')

    monkeypatch.setattr(recorder, '_spawn_ffmpeg', boom)
    resp = client.post('/api/meetings/start')
    assert resp.status_code == 500
    assert client.get('/api/meetings').get_json() == []


def test_stop_awaits_transcription_start(client, rec):
    meeting_id = _start(client)
    resp = client.post(f'/api/meetings/{meeting_id}/stop')
    assert resp.status_code == 200
    # Transcription no longer starts automatically — the pipeline is only
    # spawned once the user picks a model/device via /start-transcription.
    assert rec['pipeline_calls'] == []
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['status'] == 'transcribing'
    assert m['phase'] == 'awaiting_start'
    assert m['endedAt'] is not None
    assert m['durationSeconds'] is not None
    # Both ffmpeg procs were stopped gracefully.
    assert all(p.returncode is not None for _, _, p in rec['spawned'])


def test_stop_non_recording_conflicts(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    assert client.post(f'/api/meetings/{meeting_id}/stop').status_code == 409
    assert client.post('/api/meetings/unknown/stop').status_code == 404


def test_active_endpoint(client, rec):
    assert client.get('/api/meetings/active').get_json() == {'id': None, 'startedAt': None}
    meeting_id = _start(client)
    active = client.get('/api/meetings/active').get_json()
    assert active['id'] == meeting_id
    assert active['startedAt'] is not None
    client.post(f'/api/meetings/{meeting_id}/stop')
    assert client.get('/api/meetings/active').get_json()['id'] is None


def test_patch_title_and_notes(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    resp = client.patch(f'/api/meetings/{meeting_id}',
                        json={'title': 'Standup', 'notes': 'follow up with Sam'})
    assert resp.status_code == 200
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['title'] == 'Standup'
    assert m['notes'] == 'follow up with Sam'
    assert client.patch('/api/meetings/unknown', json={'title': 'x'}).status_code == 404


def test_delete(client, rec, tmp_path):
    meeting_id = _start(client)
    # Deleting mid-recording is refused.
    assert client.delete(f'/api/meetings/{meeting_id}').status_code == 409
    client.post(f'/api/meetings/{meeting_id}/stop')
    audio_dir = tmp_path / 'meetings' / meeting_id
    assert audio_dir.is_dir()
    assert client.delete(f'/api/meetings/{meeting_id}').status_code == 200
    assert client.get('/api/meetings').get_json() == []
    assert not audio_dir.exists()


def test_audio_endpoint(client, rec, tmp_path):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    assert client.get(f'/api/meetings/{meeting_id}/audio/mic').status_code == 404  # no file written
    assert client.get(f'/api/meetings/{meeting_id}/audio/nope').status_code == 404
    (tmp_path / 'meetings' / meeting_id / 'mic.wav').write_bytes(b'RIFFfake')
    resp = client.get(f'/api/meetings/{meeting_id}/audio/mic')
    assert resp.status_code == 200
    assert resp.mimetype == 'audio/wav'


def test_rename_speakers_round_trip(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')

    names = {'Speaker 1': 'Alice', 'Me': 'Volodya'}
    assert client.patch(f'/api/meetings/{meeting_id}', json={'speakerNames': names}).status_code == 200
    assert client.get(f'/api/meetings/{meeting_id}').get_json()['speakerNames'] == names

    # Clearing the mapping reverts to canonical labels.
    assert client.patch(f'/api/meetings/{meeting_id}', json={'speakerNames': None}).status_code == 200
    assert client.get(f'/api/meetings/{meeting_id}').get_json()['speakerNames'] is None


def test_rename_speakers_rejects_bad_payloads(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    # (Non-string keys can't occur: JSON object keys are always strings on the wire.)
    for bad in (['Alice'], 'Alice', {'Speaker 1': 3}):
        resp = client.patch(f'/api/meetings/{meeting_id}', json={'speakerNames': bad})
        assert resp.status_code == 400, f'payload {bad!r} should be rejected'


def test_summarize_uses_renamed_speakers(client, rec, monkeypatch):
    import json as _json
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')

    from backend.db.connection import get_db
    segments = [
        {'start': 0.0, 'end': 2.0, 'speaker': 'Me', 'text': 'hello'},
        {'start': 3.0, 'end': 5.0, 'speaker': 'Speaker 1', 'text': 'hi there'},
    ]
    get_db().execute(
        "UPDATE meetings SET status='done', phase='done', segments=?, transcript_text=? WHERE id=?",
        (_json.dumps(segments), '[00:00] Me: hello\n[00:03] Speaker 1: hi there', meeting_id),
    )
    get_db().commit()
    client.patch(f'/api/meetings/{meeting_id}', json={'speakerNames': {'Speaker 1': 'Alice'}})

    seen = {}

    def fake_summarize(text):
        seen['text'] = text
        return 'ok'

    monkeypatch.setattr('backend.ai.meetings.summarize_meeting', fake_summarize)
    resp = client.post(f'/api/meetings/{meeting_id}/summarize')
    assert resp.status_code == 200
    assert 'Alice: hi there' in seen['text']
    assert 'Speaker 1' not in seen['text']
    assert 'Me: hello' in seen['text']


def _upload(client, filename='recording.mp3', title=None, duration=12.5, monkeypatch=None,
            transcode=None):
    if monkeypatch is not None:
        monkeypatch.setattr(storage, 'transcode_to_system_track',
                            transcode or (lambda src, dest: duration))
    data = {'audio': (io.BytesIO(b'fake audio bytes'), filename)}
    if title is not None:
        data['title'] = title
    return client.post('/api/meetings/upload', data=data, content_type='multipart/form-data')


def test_upload_creates_meeting_row(client, rec, monkeypatch):
    resp = _upload(client, monkeypatch=monkeypatch, duration=12.5)
    assert resp.status_code == 201
    meeting_id = resp.get_json()['id']
    # Uploads also await a manual model/device choice before transcribing.
    assert rec['pipeline_calls'] == []

    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['status'] == 'transcribing'
    assert m['phase'] == 'awaiting_start'
    assert m['source'] == 'upload'
    assert m['durationSeconds'] == 12.5
    assert m['title'] is None


def test_upload_with_title(client, rec, monkeypatch):
    resp = _upload(client, title='Standup recap', monkeypatch=monkeypatch)
    meeting_id = resp.get_json()['id']
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['title'] == 'Standup recap'


def test_upload_missing_file(client, rec):
    resp = client.post('/api/meetings/upload', data={}, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert client.get('/api/meetings').get_json() == []


def test_upload_bad_audio_rolls_back(client, rec, monkeypatch, tmp_path):
    def boom(src, dest):
        raise subprocess.CalledProcessError(1, ['ffmpeg'])

    resp = _upload(client, monkeypatch=monkeypatch, transcode=boom)
    assert resp.status_code == 400
    assert client.get('/api/meetings').get_json() == []
    assert list((tmp_path / 'meetings').iterdir()) == []


def test_transcode_to_system_track_real_ffmpeg(tmp_path):
    """Exercises the real ffmpeg/ffprobe calls (no test hits the network or
    an AI provider here, so this is worth running for real rather than mocking)."""
    src = tmp_path / 'source.wav'
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
         '-i', 'sine=frequency=440:duration=2', str(src)],
        check=True,
    )
    dest = tmp_path / 'system.wav'
    duration = storage.transcode_to_system_track(src, dest)
    assert dest.is_file()
    assert duration == pytest.approx(2.0, abs=0.2)


def _set_phase(meeting_id, phase, status='transcribing'):
    from backend.db.connection import get_db
    get_db().execute('UPDATE meetings SET status=?, phase=? WHERE id=?', (status, phase, meeting_id))
    get_db().commit()


def _start_transcription(client, meeting_id, model='large-v3', device='cpu'):
    return client.post(f'/api/meetings/{meeting_id}/start-transcription',
                       json={'whisperModel': model, 'device': device})


def test_start_transcription_success_and_conflicts(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')  # phase='awaiting_start'

    resp = _start_transcription(client, meeting_id, model='turbo', device='cuda')
    assert resp.status_code == 200
    assert rec['pipeline_calls'] == [meeting_id]
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['phase'] == 'transcribing_mic'
    assert m['status'] == 'transcribing'
    assert m['whisperModel'] == 'turbo'
    assert m['whisperDevice'] == 'cuda'

    for phase, status in [
        ('recording', 'recording'), ('transcribing_mic', 'transcribing'),
        ('transcribing_system', 'transcribing'), ('diarizing', 'transcribing'),
        ('summarizing', 'transcribing'), ('done', 'done'), ('error', 'error'),
        ('paused_mic', 'transcribing'), ('paused_system', 'transcribing'),
    ]:
        _set_phase(meeting_id, phase, status)
        resp = _start_transcription(client, meeting_id)
        assert resp.status_code == 409, f'phase={phase!r} should reject start-transcription'

    assert _start_transcription(client, 'unknown').status_code == 404


def test_start_transcription_routes_upload_to_system_phase(client, rec, monkeypatch):
    resp = _upload(client, monkeypatch=monkeypatch)
    meeting_id = resp.get_json()['id']

    assert _start_transcription(client, meeting_id).status_code == 200
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['phase'] == 'transcribing_system'


def test_start_transcription_validates_body(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')

    assert client.post(f'/api/meetings/{meeting_id}/start-transcription',
                       json={'device': 'cpu'}).status_code == 400
    assert client.post(f'/api/meetings/{meeting_id}/start-transcription',
                       json={'whisperModel': 'turbo', 'device': 'tpu'}).status_code == 400
    assert rec['pipeline_calls'] == []


def test_retry_success_and_conflicts(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    _start_transcription(client, meeting_id, model='large-v3', device='cuda')

    # Simulate a CUDA OOM: pipeline failed mid-mic-track, leaving phase
    # exactly where it was (not overwritten to a generic 'error').
    _set_phase(meeting_id, 'transcribing_mic', status='error')
    from backend.db.connection import get_db
    get_db().execute("UPDATE meetings SET error='CUDA out of memory' WHERE id=?", (meeting_id,))
    get_db().commit()

    resp = client.post(f'/api/meetings/{meeting_id}/retry',
                       json={'whisperModel': 'tiny', 'device': 'cpu'})
    assert resp.status_code == 200
    assert rec['pipeline_calls'] == [meeting_id, meeting_id]  # start + retry
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['status'] == 'transcribing'
    assert m['phase'] == 'transcribing_mic'  # resume point preserved, not reset
    assert m['whisperModel'] == 'tiny'
    assert m['whisperDevice'] == 'cpu'
    assert m['error'] is None

    for phase, status in [
        ('recording', 'recording'), ('awaiting_start', 'transcribing'),
        ('transcribing_mic', 'transcribing'), ('transcribing_system', 'transcribing'),
        ('diarizing', 'transcribing'), ('summarizing', 'transcribing'),
        ('paused_mic', 'transcribing'), ('paused_system', 'transcribing'),
        ('done', 'done'),
    ]:
        _set_phase(meeting_id, phase, status)
        resp = client.post(f'/api/meetings/{meeting_id}/retry',
                           json={'whisperModel': 'tiny', 'device': 'cpu'})
        assert resp.status_code == 409, f'status={status!r} phase={phase!r} should reject retry'

    assert client.post('/api/meetings/unknown/retry',
                       json={'whisperModel': 'tiny', 'device': 'cpu'}).status_code == 404


def test_retry_validates_body(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    _set_phase(meeting_id, 'transcribing_mic', status='error')

    assert client.post(f'/api/meetings/{meeting_id}/retry',
                       json={'device': 'cpu'}).status_code == 400
    assert client.post(f'/api/meetings/{meeting_id}/retry',
                       json={'whisperModel': 'tiny', 'device': 'tpu'}).status_code == 400
    assert rec['pipeline_calls'] == []


def test_redo_from_done_clears_transcript_and_checkpoints(client, rec):
    import json as _json
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    _start_transcription(client, meeting_id, model='large-v3', device='cpu')

    from backend.db.connection import get_db
    segments = [{'start': 0.0, 'end': 2.0, 'speaker': 'Me', 'text': 'hello'}]
    get_db().execute(
        "UPDATE meetings SET status='done', phase='done', segments=?, transcript_text=?,"
        " summary=?, speaker_names=?, mic_offset_seconds=12, mic_segments_partial=?,"
        " system_offset_seconds=8, system_segments_partial=? WHERE id=?",
        (_json.dumps(segments), '[00:00] Me: hello', 'a summary',
         _json.dumps({'Me': 'Volodya'}), _json.dumps(segments), _json.dumps(segments), meeting_id),
    )
    get_db().commit()

    resp = client.post(f'/api/meetings/{meeting_id}/redo',
                       json={'whisperModel': 'turbo', 'device': 'cuda'})
    assert resp.status_code == 200
    assert rec['pipeline_calls'] == [meeting_id, meeting_id]  # start + redo

    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['status'] == 'transcribing'
    assert m['phase'] == 'transcribing_mic'
    assert m['whisperModel'] == 'turbo'
    assert m['whisperDevice'] == 'cuda'
    assert m['segments'] is None
    assert m['transcriptText'] is None
    assert m['summary'] is None
    assert m['speakerNames'] is None

    row = get_db().execute(
        'SELECT mic_offset_seconds, mic_segments_partial,'
        ' system_offset_seconds, system_segments_partial FROM meetings WHERE id=?',
        (meeting_id,)).fetchone()
    assert row['mic_offset_seconds'] == 0
    assert row['mic_segments_partial'] is None
    assert row['system_offset_seconds'] == 0
    assert row['system_segments_partial'] is None


def test_redo_from_error_and_upload_routing(client, rec, monkeypatch):
    resp = _upload(client, monkeypatch=monkeypatch)
    meeting_id = resp.get_json()['id']
    _set_phase(meeting_id, 'transcribing_system', status='error')

    resp = client.post(f'/api/meetings/{meeting_id}/redo',
                       json={'whisperModel': 'tiny', 'device': 'cpu'})
    assert resp.status_code == 200
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    # Uploads have no mic track — redo must route straight to the system phase.
    assert m['phase'] == 'transcribing_system'
    assert m['error'] is None


def test_redo_conflicts_and_validation(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')

    for phase, status in [
        ('recording', 'recording'), ('awaiting_start', 'transcribing'),
        ('transcribing_mic', 'transcribing'), ('transcribing_system', 'transcribing'),
        ('diarizing', 'transcribing'), ('summarizing', 'transcribing'),
        ('paused_mic', 'transcribing'), ('paused_system', 'transcribing'),
    ]:
        _set_phase(meeting_id, phase, status)
        resp = client.post(f'/api/meetings/{meeting_id}/redo',
                           json={'whisperModel': 'tiny', 'device': 'cpu'})
        assert resp.status_code == 409, f'status={status!r} phase={phase!r} should reject redo'

    _set_phase(meeting_id, 'done', status='done')
    assert client.post(f'/api/meetings/{meeting_id}/redo',
                       json={'device': 'cpu'}).status_code == 400
    assert client.post(f'/api/meetings/{meeting_id}/redo',
                       json={'whisperModel': 'tiny', 'device': 'tpu'}).status_code == 400
    assert rec['pipeline_calls'] == []

    assert client.post('/api/meetings/unknown/redo',
                       json={'whisperModel': 'tiny', 'device': 'cpu'}).status_code == 404


def test_pause_success_and_conflicts(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    _start_transcription(client, meeting_id)  # phase='transcribing_mic'

    resp = client.post(f'/api/meetings/{meeting_id}/pause')
    assert resp.status_code == 200
    from backend.db.connection import get_db
    row = get_db().execute('SELECT pause_requested FROM meetings WHERE id=?', (meeting_id,)).fetchone()
    assert row['pause_requested'] == 1

    _set_phase(meeting_id, 'transcribing_system')
    assert client.post(f'/api/meetings/{meeting_id}/pause').status_code == 200

    for phase, status in [
        ('recording', 'recording'), ('awaiting_start', 'transcribing'),
        ('diarizing', 'transcribing'),
        ('summarizing', 'transcribing'), ('done', 'done'), ('error', 'error'),
        ('paused_mic', 'transcribing'), ('paused_system', 'transcribing'),
    ]:
        _set_phase(meeting_id, phase, status)
        resp = client.post(f'/api/meetings/{meeting_id}/pause')
        assert resp.status_code == 409, f'phase={phase!r} should reject pause'

    assert client.post('/api/meetings/unknown/pause').status_code == 404


def test_resume_success_and_conflicts(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')

    _set_phase(meeting_id, 'paused_mic')
    resp = client.post(f'/api/meetings/{meeting_id}/resume')
    assert resp.status_code == 200
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['phase'] == 'transcribing_mic'
    assert m['status'] == 'transcribing'

    _set_phase(meeting_id, 'paused_system')
    resp = client.post(f'/api/meetings/{meeting_id}/resume')
    assert resp.status_code == 200
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['phase'] == 'transcribing_system'
    assert m['status'] == 'transcribing'
    # Both successful /resume calls each spawn the pipeline once (/stop no
    # longer does, since transcription now awaits a manual start).
    assert rec['pipeline_calls'] == [meeting_id, meeting_id]

    for phase, status in [
        ('recording', 'recording'), ('awaiting_start', 'transcribing'),
        ('transcribing_mic', 'transcribing'),
        ('transcribing_system', 'transcribing'), ('diarizing', 'transcribing'),
        ('summarizing', 'transcribing'), ('done', 'done'), ('error', 'error'),
    ]:
        _set_phase(meeting_id, phase, status)
        resp = client.post(f'/api/meetings/{meeting_id}/resume')
        assert resp.status_code == 409, f'phase={phase!r} should reject resume'

    assert client.post('/api/meetings/unknown/resume').status_code == 404


def test_double_resume_race_only_one_succeeds(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    _set_phase(meeting_id, 'paused_mic')

    codes = sorted([
        client.post(f'/api/meetings/{meeting_id}/resume').status_code,
        client.post(f'/api/meetings/{meeting_id}/resume').status_code,
    ])
    assert codes == [200, 409]
    # Only the single successful /resume spawns the pipeline.
    assert rec['pipeline_calls'] == [meeting_id]


def test_internal_pause_columns_not_exposed(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')
    _start_transcription(client, meeting_id)
    client.post(f'/api/meetings/{meeting_id}/pause')
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    for key in ('pauseRequested', 'micOffsetSeconds', 'micSegmentsPartial',
               'systemOffsetSeconds', 'systemSegmentsPartial'):
        assert key not in m


def test_reset_stale_meetings(client, rec):
    meeting_id = _start(client)
    from backend.db.connection import get_db, _reset_stale_meetings
    db = get_db()
    _reset_stale_meetings(db)
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['status'] == 'error'
    assert 'restart' in m['error']


def test_reset_stale_meetings_leaves_paused_meetings_untouched(client, rec):
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')

    for phase in ('paused_mic', 'paused_system'):
        _set_phase(meeting_id, phase)
        from backend.db.connection import get_db, _reset_stale_meetings
        _reset_stale_meetings(get_db())
        m = client.get(f'/api/meetings/{meeting_id}').get_json()
        assert m['phase'] == phase
        assert m['status'] == 'transcribing'
        assert m['error'] is None


def test_reset_stale_meetings_leaves_awaiting_start_untouched(client, rec):
    """A meeting parked at awaiting_start has no thread running for it — an
    app restart must not error it out, same as the paused states."""
    meeting_id = _start(client)
    client.post(f'/api/meetings/{meeting_id}/stop')  # phase='awaiting_start'

    from backend.db.connection import get_db, _reset_stale_meetings
    _reset_stale_meetings(get_db())
    m = client.get(f'/api/meetings/{meeting_id}').get_json()
    assert m['phase'] == 'awaiting_start'
    assert m['status'] == 'transcribing'
    assert m['error'] is None
