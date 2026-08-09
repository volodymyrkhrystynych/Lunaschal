"""Unit tests for backend.ai.caption_image.

This module went without a test file for as long as it went without a working
model: the Settings checkbox wrote `gemma4-vision`, a preset that was never
defined in llama/presets.ini, so every caption 404'd at the router and the
feature looked merely unconfigured. Pointing it at the real `[gemma4-12b-omni]`
preset exposed a second failure sitting right behind the first, which is what
`test_caption_turns_thinking_off` is about.
"""
import base64

import pytest

from backend.ai import images as images_ai
from backend.ai.images import VisionUnavailable


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


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'gemma4-12b-omni'}
    )


def _png(tmp_path, name='photo.png'):
    p = tmp_path / name
    p.write_bytes(b'\x89PNG\r\n\x1a\nfake')
    return p


def test_get_vision_model_reads_the_configured_alias(monkeypatch):
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': ' gemma4-12b-omni '}
    )
    assert images_ai.get_vision_model() == 'gemma4-12b-omni'
    assert images_ai.is_vision_configured() is True


@pytest.mark.parametrize('value', [None, '', '   '])
def test_captioning_is_off_when_unconfigured(monkeypatch, tmp_path, value):
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': value}
    )
    assert images_ai.is_vision_configured() is False
    with pytest.raises(VisionUnavailable, match='No vision model configured'):
        images_ai.caption_image(_png(tmp_path))


def test_caption_turns_thinking_off(configured, monkeypatch, tmp_path):
    """The regression this file exists for.

    Gemma 4's chat template defaults thinking *on*, and `caption_image` builds
    its own request rather than going through backend/ai/llm.py's
    `_request_kwargs`, so nothing else switches it off. Measured against the live
    model: with thinking on the 300-token budget goes entirely to reasoning and
    `content` is empty, which surfaces as "The model returned an empty
    description" — indistinguishable from a model that could not see the image.
    """
    client = _FakeClient(content='A red circle beside a blue rectangle.')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    images_ai.caption_image(_png(tmp_path))

    kwargs = client.completions.calls[0]['extra_body']['chat_template_kwargs']
    assert kwargs['enable_thinking'] is False


def test_caption_sends_the_image_and_the_hint(configured, monkeypatch, tmp_path):
    client = _FakeClient(content='  A leaking pipe under a sink.  ')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)
    p = _png(tmp_path)

    result = images_ai.caption_image(p, hint='the leak under the sink')

    assert result == 'A leaking pipe under a sink.'
    call = client.completions.calls[0]
    assert call['model'] == 'gemma4-12b-omni'
    text, image = call['messages'][1]['content']
    assert 'the leak under the sink' in text['text']
    expected = base64.b64encode(p.read_bytes()).decode('ascii')
    assert image['image_url']['url'] == f'data:image/png;base64,{expected}'


def test_an_empty_description_is_an_error_not_an_empty_caption(
    configured, monkeypatch, tmp_path
):
    client = _FakeClient(content='   ')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    with pytest.raises(VisionUnavailable, match='empty description'):
        images_ai.caption_image(_png(tmp_path))


def test_heic_is_refused_before_the_model_is_called(configured, monkeypatch, tmp_path):
    """storage.IMAGE_EXTS accepts heic; the projector path cannot read it. The
    refusal has to name the fix, because "convert to JPEG" is something only the
    user can do."""
    client = _FakeClient(content='never reached')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    with pytest.raises(VisionUnavailable, match='convert to JPEG or PNG'):
        images_ai.caption_image(_png(tmp_path, 'holiday.heic'))
    assert client.completions.calls == []


def test_a_missing_file_is_reported_as_such(configured, tmp_path):
    with pytest.raises(VisionUnavailable, match='image file is missing'):
        images_ai.caption_image(tmp_path / 'gone.png')


def test_client_errors_are_wrapped(configured, monkeypatch, tmp_path):
    monkeypatch.setattr(
        images_ai, 'get_llama_client',
        lambda: _FakeClient(error=RuntimeError('connection refused'))
    )
    with pytest.raises(VisionUnavailable, match='connection refused'):
        images_ai.caption_image(_png(tmp_path))
