"""Tests for the STT device (GPU/CPU) setting."""
import sys
import warnings

import pytest

from backend.routes import stt


@pytest.fixture(autouse=True)
def reset_stt_state(monkeypatch):
    monkeypatch.setattr(stt, 'DEVICE', 'cuda')
    monkeypatch.setattr(stt, 'MODEL_NAME', 'turbo')
    monkeypatch.setattr(stt, 'STT_BACKEND', 'local')
    stt._stt_model = None
    stt._stt_ready = False
    stt._loaded_stt_backend = None
    stt._loaded_model_name = None
    stt._loaded_device = None
    yield


def test_get_active_stt_device_falls_back_to_env_default(client):
    assert stt._get_active_stt_device() == 'cuda'


def test_get_active_stt_device_reads_db_setting(client):
    client.patch('/api/settings/ai', json={'sttDevice': 'cpu'})
    assert stt._get_active_stt_device() == 'cpu'


def test_settings_roundtrip_stt_device(client):
    resp = client.patch('/api/settings/ai', json={'sttDevice': 'cpu'})
    assert resp.status_code == 200
    settings = client.get('/api/settings').get_json()
    assert settings['sttDevice'] == 'cpu'


def test_load_stt_loads_whisper_on_configured_device(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'whisper', _FakeWhisperModule(calls))

    client.patch('/api/settings/ai', json={'sttDevice': 'cpu'})
    stt._load_stt()

    assert calls == [('turbo', 'cpu')]
    assert stt._loaded_device == 'cpu'


def test_load_stt_reloads_when_device_setting_changes(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'whisper', _FakeWhisperModule(calls))

    stt._load_stt()  # defaults to 'cuda' (no DB setting yet)
    assert calls == [('turbo', 'cuda')]

    client.patch('/api/settings/ai', json={'sttDevice': 'cpu'})
    stt._load_stt()  # device changed -> must reload, not hit the fast path

    assert calls == [('turbo', 'cuda'), ('turbo', 'cpu')]
    assert stt._loaded_device == 'cpu'


def test_load_stt_skips_reload_when_config_unchanged(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'whisper', _FakeWhisperModule(calls))

    stt._load_stt()
    stt._load_stt()

    assert calls == [('turbo', 'cuda')]  # second call hit the fast path, no reload


def test_do_transcribe_disables_fp16_probe_on_cpu(client):
    transcribe_opts = []

    class FakeModel:
        def transcribe(self, path, **opts):
            transcribe_opts.append(opts)
            return {'text': 'hi', 'language': 'en'}

    stt._stt_model = FakeModel()
    stt._loaded_stt_backend = 'local'
    stt._loaded_device = 'cpu'

    stt._do_transcribe(b'\x00' * 2000, 'rec.wav', None)

    assert transcribe_opts[0]['fp16'] is False


def test_do_transcribe_leaves_fp16_alone_on_gpu(client):
    transcribe_opts = []

    class FakeModel:
        def transcribe(self, path, **opts):
            transcribe_opts.append(opts)
            return {'text': 'hi', 'language': 'en'}

    stt._stt_model = FakeModel()
    stt._loaded_stt_backend = 'local'
    stt._loaded_device = 'cuda'

    stt._do_transcribe(b'\x00' * 2000, 'rec.wav', None)

    assert 'fp16' not in transcribe_opts[0]


def test_do_transcribe_suppresses_expected_cpu_warning(client, recwarn):
    class FakeModel:
        def transcribe(self, path, **opts):
            warnings.warn('Performing inference on CPU when CUDA is available')
            return {'text': 'hi', 'language': 'en'}

    stt._stt_model = FakeModel()
    stt._loaded_stt_backend = 'local'
    stt._loaded_device = 'cpu'

    stt._do_transcribe(b'\x00' * 2000, 'rec.wav', None)

    assert len(recwarn) == 0


def test_do_transcribe_does_not_suppress_warnings_on_gpu(client, recwarn):
    class FakeModel:
        def transcribe(self, path, **opts):
            warnings.warn('some other unrelated warning')
            return {'text': 'hi', 'language': 'en'}

    stt._stt_model = FakeModel()
    stt._loaded_stt_backend = 'local'
    stt._loaded_device = 'cuda'

    stt._do_transcribe(b'\x00' * 2000, 'rec.wav', None)

    assert len(recwarn) == 1


class _FakeWhisperModel:
    pass


class _FakeWhisperModule:
    def __init__(self, calls):
        self._calls = calls

    def load_model(self, model_name, device):
        self._calls.append((model_name, device))
        return _FakeWhisperModel()
