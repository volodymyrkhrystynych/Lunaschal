"""Tests for the Granite Speech STT backend."""
import sys

import numpy as np
import pytest
import soundfile as sf
import torch

from backend.routes import stt


@pytest.fixture(autouse=True)
def reset_stt_state(monkeypatch):
    monkeypatch.setattr(stt, 'DEVICE', 'cuda')
    monkeypatch.setattr(stt, 'MODEL_NAME', 'turbo')
    monkeypatch.setattr(stt, 'STT_BACKEND', 'local')
    stt._stt_model = None
    stt._granite_processor = None
    stt._stt_ready = False
    stt._loaded_stt_backend = None
    stt._loaded_model_name = None
    stt._loaded_device = None
    yield


def _write_fake_wav(tmp_path):
    path = tmp_path / 'decoded.wav'
    sf.write(str(path), np.zeros(1600, dtype='float32'), 16000)
    return str(path)


class _FakeTokenizer:
    def apply_chat_template(self, chat, tokenize=False, add_generation_prompt=True):
        return 'PROMPT'

    def batch_decode(self, tokens, add_special_tokens=False, skip_special_tokens=True):
        return ['hello granite']


class _FakeModelInputs(dict):
    def to(self, device):
        return self


class _FakeGraniteProcessor:
    def __init__(self):
        self.tokenizer = _FakeTokenizer()
        self.calls = []

    def __call__(self, text_prompt, wav, device=None, return_tensors=None):
        self.calls.append((text_prompt, device))
        return _FakeModelInputs(input_ids=torch.zeros((1, 5), dtype=torch.long))


class _FakeGraniteModel:
    def __init__(self, calls):
        self._calls = calls

    def generate(self, **kwargs):
        self._calls.append(kwargs)
        return torch.arange(10).unsqueeze(0)


class _FakeGraniteModelInstance:
    def eval(self):
        return self


class _FakeTransformersModule:
    """Fake `transformers` module recording from_pretrained() calls as
    ('processor'|'model', model_name, [device_map, torch_dtype]) tuples."""

    def __init__(self, calls):
        outer = self
        self._calls = calls

        class _AutoProcessor:
            @staticmethod
            def from_pretrained(model_name):
                outer._calls.append(('processor', model_name))
                return _FakeGraniteProcessor()

        class _AutoModelForSpeechSeq2Seq:
            @staticmethod
            def from_pretrained(model_name, device_map=None, torch_dtype=None):
                outer._calls.append(('model', model_name, device_map, torch_dtype))
                return _FakeGraniteModelInstance()

        self.AutoProcessor = _AutoProcessor
        self.AutoModelForSpeechSeq2Seq = _AutoModelForSpeechSeq2Seq


def test_load_stt_loads_granite_model(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'transformers', _FakeTransformersModule(calls))
    client.patch('/api/settings/ai', json={'sttBackend': 'granite', 'sttDevice': 'cpu'})

    stt._load_stt()

    assert stt._loaded_stt_backend == 'granite'
    assert stt._loaded_model_name == stt.GRANITE_STT_MODEL
    assert ('processor', stt.GRANITE_STT_MODEL) in calls
    assert ('model', stt.GRANITE_STT_MODEL, 'cpu', torch.float32) in calls


def test_load_stt_skips_reload_when_granite_already_loaded(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'transformers', _FakeTransformersModule(calls))
    client.patch('/api/settings/ai', json={'sttBackend': 'granite', 'sttDevice': 'cpu'})

    stt._load_stt()
    stt._load_stt()

    assert len([c for c in calls if c[0] == 'model']) == 1


def test_load_stt_reloads_granite_when_device_changes(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'transformers', _FakeTransformersModule(calls))
    client.patch('/api/settings/ai', json={'sttBackend': 'granite', 'sttDevice': 'cpu'})
    stt._load_stt()

    client.patch('/api/settings/ai', json={'sttDevice': 'cuda'})
    stt._load_stt()

    model_calls = [c for c in calls if c[0] == 'model']
    assert len(model_calls) == 2
    assert model_calls[0][2] == 'cpu'
    assert model_calls[1][2] == 'cuda'


def test_do_transcribe_granite_decodes_and_generates(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stt, '_ffmpeg_decode_16k_mono', lambda src_path: _write_fake_wav(tmp_path))
    model_calls = []
    stt._granite_processor = _FakeGraniteProcessor()
    stt._stt_model = _FakeGraniteModel(model_calls)
    stt._loaded_stt_backend = 'granite'
    stt._loaded_device = 'cpu'

    result = stt._do_transcribe(b'\x00' * 2000, 'rec.webm', None)

    assert result == {'text': 'hello granite', 'language': 'en'}
    assert len(model_calls) == 1


def test_do_transcribe_granite_resets_model_on_error(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stt, '_ffmpeg_decode_16k_mono', lambda src_path: _write_fake_wav(tmp_path))

    class _FailingModel:
        def generate(self, **kwargs):
            raise RuntimeError('boom')

    stt._granite_processor = _FakeGraniteProcessor()
    stt._stt_model = _FailingModel()
    stt._loaded_stt_backend = 'granite'
    stt._loaded_device = 'cpu'
    stt._stt_ready = True

    with pytest.raises(RuntimeError):
        stt._do_transcribe(b'\x00' * 2000, 'rec.webm', None)

    assert stt._stt_ready is False
    assert stt._loaded_stt_backend is None
    assert stt._granite_processor is None


def test_stt_health_reports_granite_backend_and_model(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'transformers', _FakeTransformersModule(calls))
    client.patch('/api/settings/ai', json={'sttBackend': 'granite', 'sttDevice': 'cpu'})
    stt._load_stt()

    data = client.get('/api/stt/health').get_json()

    assert data['stt_backend'] == 'granite'
    assert data['stt_model'] == stt.GRANITE_STT_MODEL
    assert data['stt_ready'] is True


def test_stt_reload_clears_granite_processor(client, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'transformers', _FakeTransformersModule(calls))
    client.patch('/api/settings/ai', json={'sttBackend': 'granite', 'sttDevice': 'cpu'})
    stt._load_stt()
    assert stt._granite_processor is not None

    resp = client.post('/api/stt/reload')

    assert resp.status_code == 200
    assert stt._granite_processor is None
    assert stt._stt_model is None


def test_settings_roundtrip_stt_backend_granite(client):
    resp = client.patch('/api/settings/ai', json={'sttBackend': 'granite'})
    assert resp.status_code == 200
    settings = client.get('/api/settings').get_json()
    assert settings['sttBackend'] == 'granite'
