"""Unit tests for backend.ai.caption_image.

This module went without a test file for as long as it went without a working
model: the Settings checkbox wrote `gemma4-vision`, a preset that was never
defined in llama/presets.ini, so every caption 404'd at the router and the
feature looked merely unconfigured. Pointing it at the real `[gemma4-12b-omni]`
preset exposed a second failure sitting right behind the first, which is what
`test_caption_turns_thinking_off` is about.

The fixtures write **real** images rather than a few magic bytes: `data_uri` now
decodes, rotates and downscales what it is given, so a file that isn't an image
no longer reaches the assertions this file cares about.
"""
import base64
import io

import pytest
from PIL import Image

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


def _png(tmp_path, name='photo.png', size=(64, 48)):
    p = tmp_path / name
    Image.new('RGB', size, 'red').save(p)
    return p


def _decoded(call):
    """The JPEG bytes that actually went out in a captured `create(**kwargs)`."""
    _text, image = call['messages'][1]['content']
    url = image['image_url']['url']
    assert url.startswith('data:image/jpeg;base64,')
    return base64.b64decode(url.split(',', 1)[1])


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
    text, _image = call['messages'][1]['content']
    assert 'the leak under the sink' in text['text']
    # Whatever went in, a JPEG of the same picture comes out.
    assert Image.open(io.BytesIO(_decoded(call))).size == (64, 48)


def test_a_big_photo_is_downscaled_before_it_is_sent(configured, monkeypatch, tmp_path):
    """The cost lever. A full-res phone photo is ~4100 image tokens and ~110 s of
    prompt eval through a CPU-resident projector, for a caption that gains
    nothing from the pixels."""
    client = _FakeClient(content='A wall.')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    images_ai.caption_image(_png(tmp_path, size=(5712, 4284)))

    sent = Image.open(io.BytesIO(_decoded(client.completions.calls[0])))
    assert max(sent.size) == images_ai._MAX_EDGE
    # Aspect ratio preserved: 5712/4284 is 4:3.
    assert sent.size == (1280, 960)


def test_exif_orientation_is_baked_in(configured, monkeypatch, tmp_path):
    """llama.cpp's projector reads pixels and ignores the EXIF tag, so a portrait
    photo arrives on its side — and the model says so mid-caption instead of
    describing the picture. Rotate here or not at all."""
    client = _FakeClient(content='Upright.')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    # Orientation 6: stored landscape, displayed rotated 90° clockwise.
    p = tmp_path / 'sideways.jpg'
    img = Image.new('RGB', (80, 40), 'blue')
    exif = img.getexif()
    exif[274] = 6
    img.save(p, exif=exif)

    images_ai.caption_image(p)

    assert Image.open(io.BytesIO(_decoded(client.completions.calls[0]))).size == (40, 80)


def test_an_empty_description_is_an_error_not_an_empty_caption(
    configured, monkeypatch, tmp_path
):
    client = _FakeClient(content='   ')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    with pytest.raises(VisionUnavailable, match='empty description'):
        images_ai.caption_image(_png(tmp_path))


def test_heic_is_sent_now_that_it_is_re_encoded(configured, monkeypatch, tmp_path):
    """This used to be `test_heic_is_refused_before_the_model_is_called`.

    The refusal existed because the stored bytes went out untouched and the
    projector would have received garbage. Every image is re-encoded to JPEG on
    the way out now, and `backend.imaging` registers Pillow's HEIF opener, so an
    iPhone photo that was never transcoded at upload is readable after all.
    """
    pillow_heif = pytest.importorskip('pillow_heif')
    client = _FakeClient(content='A beach.')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'holiday.heic'
    pillow_heif.from_pillow(Image.new('RGB', (32, 24), 'green')).save(p)

    assert images_ai.caption_image(p) == 'A beach.'
    assert Image.open(io.BytesIO(_decoded(client.completions.calls[0]))).size == (32, 24)


def test_a_file_that_is_not_an_image_is_refused(configured, monkeypatch, tmp_path):
    """A `file` attachment renamed, or a truncated upload. It must not reach the
    model, and the message must not read like a model failure."""
    client = _FakeClient(content='never reached')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)
    p = tmp_path / 'notes.png'
    p.write_bytes(b'this is not a png')

    with pytest.raises(VisionUnavailable, match='could not be read'):
        images_ai.caption_image(p)
    assert client.completions.calls == []


def test_an_unreadable_extension_names_the_fix(configured, monkeypatch, tmp_path):
    client = _FakeClient(content='never reached')
    monkeypatch.setattr(images_ai, 'get_llama_client', lambda: client)
    p = tmp_path / 'scan.tiff'
    p.write_bytes(b'whatever')

    with pytest.raises(VisionUnavailable, match='convert to JPEG or PNG'):
        images_ai.caption_image(p)
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


def test_an_alias_the_router_does_not_know_is_named_in_the_error(
    configured, monkeypatch, tmp_path
):
    """The `gemma4-vision` failure mode, made legible.

    For months this surfaced on the attachment row as a raw `openai` 400. It is a
    Settings problem and has to read like one.
    """
    monkeypatch.setattr(
        images_ai, 'get_llama_client',
        lambda: _FakeClient(error=RuntimeError(
            "Error code: 400 - {'error': {'code': 400, 'message': \"model"
            " 'gemma4-12b-omni' not found\", 'type': 'invalid_request_error'}}"
        ))
    )
    with pytest.raises(VisionUnavailable) as exc:
        images_ai.caption_image(_png(tmp_path))

    assert 'gemma4-12b-omni' in str(exc.value)
    assert 'Settings' in str(exc.value)


def test_a_model_that_will_not_fit_says_so(configured, monkeypatch, tmp_path):
    """`gemma4-12b-omni` beside a resident `[qwen36]` on one 8 GB card: the router
    reports `failed to load` after llama.cpp's device-memory probe hits
    `CUDA error: out of memory`."""
    monkeypatch.setattr(
        images_ai, 'get_llama_client',
        lambda: _FakeClient(error=RuntimeError(
            'Error code: 500 - model name=gemma4-12b-omni failed to load'
        ))
    )
    with pytest.raises(VisionUnavailable, match='could not load'):
        images_ai.caption_image(_png(tmp_path))
