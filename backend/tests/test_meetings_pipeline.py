"""Pipeline tests: _run executed synchronously with whisper/pyannote/AI mocked."""
import json
import sys
import time
import types

import pytest

from backend.meetings import pipeline, storage


def _insert_meeting(meeting_id='01TESTMEETING'):
    from backend.db.connection import get_db
    now = int(time.time())
    get_db().execute(
        'INSERT INTO meetings(id, status, phase, started_at, created_at, updated_at)'
        " VALUES (?, 'transcribing', 'transcribing_mic', ?, ?, ?)",
        (meeting_id, now, now, now),
    )
    get_db().commit()
    return meeting_id


def _write_track(path, size=20000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\0' * size)


class FakeWhisperModel:
    """Returns canned segments keyed on which track file it is given."""

    def transcribe(self, path, **opts):
        assert opts.get('fp16') is False
        if path.endswith('mic.wav'):
            return {'segments': [{'start': 0.0, 'end': 2.0, 'text': ' hello from me '}]}
        return {'segments': [{'start': 3.0, 'end': 5.0, 'text': ' reply from them '}]}


@pytest.fixture
def env(client, monkeypatch, tmp_path):
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    # Inject a fake `whisper` module so the pipeline never imports torch.
    fake_whisper = types.ModuleType('whisper')
    loads = []

    def load_model(name, device=None):
        loads.append((name, device))
        return FakeWhisperModel()

    fake_whisper.load_model = load_model
    monkeypatch.setitem(sys.modules, 'whisper', fake_whisper)
    monkeypatch.setattr(pipeline, '_diarize', lambda path: None)
    monkeypatch.setattr('backend.ai.meetings.summarize_meeting', lambda text: 'The summary.')
    return {'loads': loads, 'client': client}


def _get(client, meeting_id):
    return client.get(f'/api/meetings/{meeting_id}').get_json()


def test_happy_path_without_diarization(env):
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    _write_track(storage.system_path(meeting_id))

    pipeline._run(meeting_id)

    m = _get(env['client'], meeting_id)
    assert m['status'] == 'done'
    assert m['phase'] == 'done'
    assert m['summary'] == 'The summary.'
    speakers = [s['speaker'] for s in m['segments']]
    assert speakers == ['Me', 'Others']
    assert '[00:00] Me: hello from me' in m['transcriptText']
    assert env['loads'] == [('large-v3', 'cpu')]


def test_diarized_speaker_labels(env, monkeypatch):
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    _write_track(storage.system_path(meeting_id))
    monkeypatch.setattr(pipeline, '_diarize',
                        lambda path: [{'start': 3.0, 'end': 5.0, 'speaker': 'SPEAKER_00'}])

    pipeline._run(meeting_id)

    m = _get(env['client'], meeting_id)
    assert [s['speaker'] for s in m['segments']] == ['Me', 'Speaker 1']


def test_missing_track_is_skipped(env):
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    # No system.wav at all.

    pipeline._run(meeting_id)

    m = _get(env['client'], meeting_id)
    assert m['status'] == 'done'
    assert [s['speaker'] for s in m['segments']] == ['Me']


def test_whisper_failure_marks_error(env, monkeypatch):
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))

    def boom(name, device=None):
        raise RuntimeError('model download failed')

    sys.modules['whisper'].load_model = boom
    pipeline._run(meeting_id)

    m = _get(env['client'], meeting_id)
    assert m['status'] == 'error'
    assert 'model download failed' in m['error']


def test_unconfigured_ai_still_finishes(env, monkeypatch):
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    monkeypatch.setattr('backend.ai.meetings.summarize_meeting', lambda text: None)

    pipeline._run(meeting_id)

    m = _get(env['client'], meeting_id)
    assert m['status'] == 'done'
    assert m['summary'] is None


def test_diarize_returns_none_without_token(client, monkeypatch, tmp_path):
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    monkeypatch.delenv('HF_TOKEN', raising=False)
    p = tmp_path / 'meetings' / 'x' / 'system.wav'
    _write_track(p)
    assert pipeline._diarize(p) is None
