"""Pipeline tests: _run executed synchronously with whisper/pyannote/AI mocked."""
import json
import sys
import time
import types

import numpy as np
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


# The pipeline transcribes ndarray chunks now (not file paths), so the fake
# `load_audio` embeds a marker value in the array itself for the fake model to
# key its canned response on, and returns exactly one chunk's worth of samples
# so these tests (which don't exercise chunking/pausing) see exactly one
# transcribe() call per track, matching the pre-chunking behavior they assert on.
_MIC_MARKER = 0.1
_SYSTEM_MARKER = 0.9


def _fake_load_audio(path, sr=16000):
    marker = _MIC_MARKER if str(path).endswith('mic.wav') else _SYSTEM_MARKER
    return np.full(pipeline._CHUNK_SECONDS * pipeline._SAMPLE_RATE, marker, dtype='float32')


class FakeWhisperModel:
    """Returns canned segments keyed on which track's fake audio it is given."""

    def transcribe(self, audio, **opts):
        assert opts.get('fp16') is False
        if audio[0] == _MIC_MARKER:
            return {'segments': [{'start': 0.0, 'end': 2.0, 'text': ' hello from me '}], 'language': 'en'}
        return {'segments': [{'start': 3.0, 'end': 5.0, 'text': ' reply from them '}], 'language': 'en'}


class _CountingModel:
    """Returns a distinct canned segment per call and tracks call count, for
    tests that exercise chunking/pause/resume directly."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **opts):
        assert opts.get('fp16') is False
        self.calls += 1
        return {'segments': [{'start': 0.0, 'end': 1.0, 'text': f' chunk{self.calls} '}], 'language': 'en'}


def _make_load_audio(mic_seconds, system_seconds):
    def _load(path, sr=16000):
        secs = mic_seconds if str(path).endswith('mic.wav') else system_seconds
        return np.zeros(int(secs * 16000), dtype='float32')
    return _load


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
    fake_whisper.load_audio = _fake_load_audio
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


def test_uses_meetings_row_whisper_model_and_device(env):
    """The model/device are chosen per-meeting via /start-transcription, not
    hardcoded — the pipeline must load whichever pair is on the row."""
    from backend.db.connection import get_db
    meeting_id = _insert_meeting()
    get_db().execute(
        "UPDATE meetings SET whisper_model='turbo', whisper_device='cuda' WHERE id=?",
        (meeting_id,))
    get_db().commit()
    _write_track(storage.mic_path(meeting_id))
    _write_track(storage.system_path(meeting_id))

    pipeline._run(meeting_id)

    assert env['loads'] == [('turbo', 'cuda')]


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


def test_no_mic_track_skips_mic_phase(env, monkeypatch):
    """Uploaded single-track meetings have no mic.wav at all — the phase
    label shown to the user should never claim to be transcribing a mic."""
    meeting_id = _insert_meeting()
    _write_track(storage.system_path(meeting_id))
    # No mic.wav written.

    phases = []
    real_set_phase = pipeline._set_phase

    def spy(mid, phase, **kw):
        phases.append(phase)
        return real_set_phase(mid, phase, **kw)

    monkeypatch.setattr(pipeline, '_set_phase', spy)
    pipeline._run(meeting_id)

    assert phases[0] == 'transcribing_system'
    assert 'transcribing_mic' not in phases
    m = _get(env['client'], meeting_id)
    assert m['status'] == 'done'
    assert [s['speaker'] for s in m['segments']] == ['Others']


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


# --- Pause / resume ---

def _setup_counting_pipeline(monkeypatch, mic_seconds, system_seconds):
    """Installs a fake whisper module with a call-counting model and
    path-aware fake audio lengths; returns the model so tests can inspect
    `.calls` and swap out `.transcribe` to trigger a pause mid-run."""
    monkeypatch.setattr(pipeline, '_CHUNK_SECONDS', 1)
    model = _CountingModel()
    fake_whisper = types.ModuleType('whisper')
    fake_whisper.load_model = lambda name, device=None: model
    fake_whisper.load_audio = _make_load_audio(mic_seconds, system_seconds)
    monkeypatch.setitem(sys.modules, 'whisper', fake_whisper)
    monkeypatch.setattr(pipeline, '_diarize', lambda path: None)
    monkeypatch.setattr('backend.ai.meetings.summarize_meeting', lambda text: 'summary')
    return model


def _pause_after_nth_call(get_db, meeting_id, model, n):
    """Wraps model.transcribe so a pause is requested right after the nth
    call. Returns the original transcribe so the caller can restore it (e.g.
    to let a subsequent resume finish without pausing again)."""
    real_transcribe = model.transcribe

    def wrapped(audio, **opts):
        result = real_transcribe(audio, **opts)
        if model.calls == n:
            get_db().execute('UPDATE meetings SET pause_requested=1 WHERE id=?', (meeting_id,))
            get_db().commit()
        return result

    model.transcribe = wrapped
    return real_transcribe


def test_pause_mid_mic_then_resume_continues_from_checkpoint(client, monkeypatch, tmp_path):
    from backend.db.connection import get_db
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    _write_track(storage.system_path(meeting_id))
    model = _setup_counting_pipeline(monkeypatch, mic_seconds=5, system_seconds=5)
    real_transcribe = _pause_after_nth_call(get_db, meeting_id, model, n=2)

    pipeline._run(meeting_id)

    m = _get(client, meeting_id)
    assert m['phase'] == 'paused_mic'
    assert m['status'] == 'transcribing'
    assert model.calls == 2

    row = get_db().execute(
        'SELECT mic_offset_seconds, mic_segments_partial FROM meetings WHERE id=?',
        (meeting_id,)).fetchone()
    assert row['mic_offset_seconds'] == 2.0
    assert len(json.loads(row['mic_segments_partial'])) == 2

    # Simulate the /resume route flipping phase back, then re-entering _run
    # (production spawns this in a new thread; this bypasses threading like
    # every other pipeline test).
    get_db().execute(
        "UPDATE meetings SET phase='transcribing_mic', status='transcribing' WHERE id=?",
        (meeting_id,))
    get_db().commit()
    model.transcribe = real_transcribe  # no more pausing on resume

    pipeline._run(meeting_id)

    m = _get(client, meeting_id)
    assert m['phase'] == 'done'
    assert m['status'] == 'done'
    # 2 mic chunks already done + 3 remaining mic chunks + 5 system chunks = 10.
    # If the resumed run had wrongly re-transcribed the mic track from
    # scratch, this would be 12 instead.
    assert model.calls == 10


def test_retry_after_failure_resumes_from_checkpoint_and_keeps_phase(client, monkeypatch, tmp_path):
    """Regression test for a CUDA OOM (or any transcribe() exception) mid-track:
    the failure must not overwrite `phase` to a generic 'error' — phase is the
    only record of where to resume, since the exception can strike anywhere,
    including load_model() before any chunk loop even starts."""
    from backend.db.connection import get_db
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    _write_track(storage.system_path(meeting_id))
    model = _setup_counting_pipeline(monkeypatch, mic_seconds=5, system_seconds=5)

    real_transcribe = model.transcribe

    def boom(audio, **opts):
        if model.calls == 2:
            raise RuntimeError('CUDA out of memory')
        return real_transcribe(audio, **opts)

    model.transcribe = boom
    pipeline._run(meeting_id)

    m = _get(client, meeting_id)
    assert m['status'] == 'error'
    assert 'CUDA out of memory' in m['error']
    assert m['phase'] == 'transcribing_mic'

    row = get_db().execute(
        'SELECT mic_offset_seconds, mic_segments_partial FROM meetings WHERE id=?',
        (meeting_id,)).fetchone()
    assert row['mic_offset_seconds'] == 2.0
    assert len(json.loads(row['mic_segments_partial'])) == 2

    # Simulate the /retry route: fix the fault, flip status back, re-enter _run.
    model.transcribe = real_transcribe
    get_db().execute("UPDATE meetings SET status='transcribing', error=NULL WHERE id=?", (meeting_id,))
    get_db().commit()

    pipeline._run(meeting_id)

    m = _get(client, meeting_id)
    assert m['status'] == 'done'
    # 2 mic chunks already done + 3 remaining mic + 5 system = 10 — no re-transcription.
    assert model.calls == 10


def test_pause_mid_system_track_leaves_mic_checkpoint_untouched(client, monkeypatch, tmp_path):
    from backend.db.connection import get_db
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    meeting_id = _insert_meeting()
    _write_track(storage.mic_path(meeting_id))
    _write_track(storage.system_path(meeting_id))
    # mic finishes in exactly 1 chunk; system needs several.
    model = _setup_counting_pipeline(monkeypatch, mic_seconds=1, system_seconds=5)
    # 1 mic chunk + 2 system chunks = call 3.
    _pause_after_nth_call(get_db, meeting_id, model, n=3)

    pipeline._run(meeting_id)

    m = _get(client, meeting_id)
    assert m['phase'] == 'paused_system'
    assert m['status'] == 'transcribing'
    assert model.calls == 3

    row = get_db().execute(
        'SELECT mic_offset_seconds, mic_segments_partial,'
        ' system_offset_seconds, system_segments_partial FROM meetings WHERE id=?',
        (meeting_id,)).fetchone()
    assert row['mic_offset_seconds'] == 1.0
    assert len(json.loads(row['mic_segments_partial'])) == 1
    assert row['system_offset_seconds'] == 2.0
    assert len(json.loads(row['system_segments_partial'])) == 2
