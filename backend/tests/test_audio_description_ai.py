"""Unit tests for backend.ai.audio_description.describe_audio — mirrors the
gating/error-wrapping shape of backend.ai.images.caption_image, since that
module has no test file of its own and journal attachment route tests cover
the caption path by monkeypatching it directly instead."""
import pytest

from backend.ai import audio_description as audio_ai


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content=None, error=None):
        self._content = content
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, content=None, error=None):
        self.completions = _FakeCompletions(content, error)
        self.chat = _FakeChat(self.completions)


@pytest.fixture(autouse=True)
def _skip_ffmpeg(monkeypatch):
    """describe_audio's own gating is what's under test, not ffmpeg — a real
    audio file and binary aren't needed to exercise it."""
    monkeypatch.setattr(audio_ai, '_wav_data_uri', lambda path: 'ZmFrZQ==')


def test_get_audio_model_reads_the_configured_alias(monkeypatch):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config',
        lambda: {'llama_audio_model': 'gemma4-e4b-audio'},
    )
    assert audio_ai.get_audio_model() == 'gemma4-e4b-audio'
    assert audio_ai.is_audio_configured() is True


def test_get_audio_model_is_none_when_unset(monkeypatch):
    monkeypatch.setattr(audio_ai, 'get_provider_config', lambda: {'llama_audio_model': None})
    assert audio_ai.get_audio_model() is None
    assert audio_ai.is_audio_configured() is False


def test_describe_audio_raises_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_ai, 'get_provider_config', lambda: {'llama_audio_model': ''})
    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='No audio-description model configured'):
        audio_ai.describe_audio(p)


def test_describe_audio_raises_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    with pytest.raises(audio_ai.AudioUnavailable, match='missing'):
        audio_ai.describe_audio(tmp_path / 'nope.wav')


def test_describe_audio_returns_the_model_text_and_sends_the_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    client = _FakeClient(content='  A dog barks twice.  ')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    result = audio_ai.describe_audio(p, hint='backyard recording')

    assert result == 'A dog barks twice.'
    call = client.completions.calls[0]
    assert call['model'] == 'gemma4-e4b-audio'
    user_content = call['messages'][1]['content']
    assert 'backyard recording' in user_content[0]['text']
    assert user_content[1]['type'] == 'input_audio'
    assert user_content[1]['input_audio']['format'] == 'wav'


def test_describe_audio_wraps_client_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    client = _FakeClient(error=RuntimeError('connection refused'))
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='connection refused'):
        audio_ai.describe_audio(p)


def test_describe_audio_raises_on_empty_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    client = _FakeClient(content='   ')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='empty description'):
        audio_ai.describe_audio(p)
