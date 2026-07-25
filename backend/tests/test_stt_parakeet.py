"""Tests for the Parakeet TDT (onnx-asr, CPU) STT backend."""
import sys

import numpy as np
import pytest

from backend.routes import stt


@pytest.fixture(autouse=True)
def reset_stt_state(monkeypatch):
    monkeypatch.setattr(stt, 'DEVICE', 'cuda')
    monkeypatch.setattr(stt, 'MODEL_NAME', 'turbo')
    monkeypatch.setattr(stt, 'STT_BACKEND', 'local')
    monkeypatch.setattr(stt, 'PARAKEET_MODEL', 'nemo-parakeet-tdt-0.6b-v2')
    stt._stt_model = None
    stt._stt_ready = False
    stt._loaded_stt_backend = None
    stt._loaded_model_name = None
    stt._loaded_device = None
    yield


class _FakeParakeetModel:
    def __init__(self):
        self.calls = []

    def recognize(self, waveform, sample_rate):
        self.calls.append((len(waveform), sample_rate))
        return '  hello world  '


class _FakeOnnxAsrModule:
    def __init__(self, load_calls):
        self._load_calls = load_calls

    def load_model(self, name):
        self._load_calls.append(name)
        return _FakeParakeetModel()


# --- settings + health --------------------------------------------------------

def test_settings_roundtrip_stt_backend_parakeet(client):
    resp = client.patch('/api/settings/ai', json={'sttBackend': 'parakeet'})
    assert resp.status_code == 200
    assert stt._get_active_stt_backend() == 'parakeet'
    settings = client.get('/api/settings').get_json()
    assert settings['sttBackend'] == 'parakeet'


def test_health_reports_parakeet_model_and_readiness(client):
    client.patch('/api/settings/ai', json={'sttBackend': 'parakeet'})
    stt._stt_ready = True
    stt._loaded_stt_backend = 'parakeet'
    stt._loaded_model_name = stt.PARAKEET_MODEL
    stt._loaded_device = 'cpu'

    health = client.get('/api/stt/health').get_json()
    assert health['stt_backend'] == 'parakeet'
    assert health['stt_model'] == 'nemo-parakeet-tdt-0.6b-v2'
    assert health['stt_ready'] is True


def test_health_parakeet_not_ready_when_other_backend_loaded(client):
    client.patch('/api/settings/ai', json={'sttBackend': 'parakeet'})
    stt._stt_ready = True
    stt._loaded_stt_backend = 'local'  # a stale whisper load

    health = client.get('/api/stt/health').get_json()
    assert health['stt_ready'] is False


# --- loading ------------------------------------------------------------------

def test_load_stt_loads_parakeet_via_onnx_asr(client, monkeypatch):
    load_calls = []
    monkeypatch.setitem(sys.modules, 'onnx_asr', _FakeOnnxAsrModule(load_calls))

    client.patch('/api/settings/ai', json={'sttBackend': 'parakeet'})
    stt._load_stt(backend='parakeet')

    assert load_calls == ['nemo-parakeet-tdt-0.6b-v2']
    assert stt._loaded_stt_backend == 'parakeet'
    assert stt._loaded_device == 'cpu'
    assert stt._loaded_model_name == 'nemo-parakeet-tdt-0.6b-v2'
    assert stt._stt_ready is True


def test_load_stt_parakeet_hits_fast_path_second_time(client, monkeypatch):
    load_calls = []
    monkeypatch.setitem(sys.modules, 'onnx_asr', _FakeOnnxAsrModule(load_calls))

    stt._load_stt(backend='parakeet')
    stt._load_stt(backend='parakeet')

    assert load_calls == ['nemo-parakeet-tdt-0.6b-v2']  # loaded once, cached


def test_load_stt_reloads_when_switching_away_from_parakeet(client, monkeypatch):
    load_calls = []
    monkeypatch.setitem(sys.modules, 'onnx_asr', _FakeOnnxAsrModule(load_calls))

    whisper_calls = []

    class _FakeWhisperModule:
        def load_model(self, model_name, device):
            whisper_calls.append((model_name, device))
            return object()

    monkeypatch.setitem(sys.modules, 'whisper', _FakeWhisperModule())

    stt._load_stt(backend='parakeet')
    stt._load_stt(model_name='turbo', backend='local')

    assert load_calls == ['nemo-parakeet-tdt-0.6b-v2']
    assert whisper_calls == [('turbo', 'cuda')]
    assert stt._loaded_stt_backend == 'local'


# --- transcription ------------------------------------------------------------

def test_do_transcribe_parakeet_decodes_and_recognizes(client, monkeypatch):
    fake_waveform = np.zeros(16000, dtype=np.float32)
    monkeypatch.setattr(stt, '_decode_to_16k_mono', lambda path: fake_waveform)

    model = _FakeParakeetModel()
    stt._stt_model = model
    stt._loaded_stt_backend = 'parakeet'
    stt._loaded_device = 'cpu'

    result = stt._do_transcribe(b'\x00' * 2000, 'rec.webm', None)

    assert result == {'text': 'hello world', 'language': 'en'}
    assert model.calls == [(16000, 16000)]


def test_do_transcribe_parakeet_resets_model_on_error(client, monkeypatch):
    monkeypatch.setattr(
        stt, '_decode_to_16k_mono', lambda path: np.zeros(10, dtype=np.float32))

    class _BoomModel:
        def recognize(self, waveform, sample_rate):
            raise RuntimeError('boom')

    stt._stt_model = _BoomModel()
    stt._stt_ready = True
    stt._loaded_stt_backend = 'parakeet'
    stt._loaded_device = 'cpu'

    with pytest.raises(RuntimeError, match='boom'):
        stt._do_transcribe(b'\x00' * 2000, 'rec.webm', None)

    # model was reset so the next request reloads fresh
    assert stt._stt_model is None
    assert stt._stt_ready is False
    assert stt._loaded_stt_backend is None
