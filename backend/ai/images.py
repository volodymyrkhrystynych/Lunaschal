"""Image captioning for journal photo attachments.

This is the project's only vision call, and it is gated behind its own model
alias (`llama_vision_model`) rather than reusing `llama_model` so the two can
differ. In practice they no longer have to: `[qwen36]` carries
`Qwen3.6-35B-A3B-mmproj-F16.gguf` with `mmproj-offload = false`, so the vision
tower lives in system RAM and costs the card nothing, and `/v1/models` reports
the alias as `input_modalities: ["text", "image"]`. Settings offers whichever
aliases the router says take images, and `_repoint_vision_at_qwen36` moves an
existing dead value onto `qwen36`.

`[gemma4-12b-omni]` remains the alias for `backend/ai/audio_description.py`:
its projector is the only one reporting `has_audio_encoder`. It is a poor
*vision* choice on a single 8 GB card — measured, not assumed: with `[qwen36]`
resident the router cannot load it at all, dying in llama.cpp's device-memory
probe with `CUDA error: out of memory` even though the preset pins it to
`n-gpu-layers 0`.

Captioning stays off while the alias is unset, and `VisionUnavailable` is what
the UI shows instead of a mysteriously dead button.

Historical note worth keeping: this used to tell you to create a `[gemma4-vision]`
preset, and the Settings checkbox wrote that alias, but no such section ever
existed in `llama/presets.ini`. Captioning had never once worked; it 404'd at the
router, and the error surfaced as attachment text rather than as anything
identifying a missing preset. A gate that names a model nobody defined fails
exactly like a model that is merely slow to load — which is why `describe_image`
now recognises that specific refusal and says which alias was missing.
"""
import base64
import io
import logging
from pathlib import Path

from backend.ai.provider import get_llama_client, get_provider_config

logger = logging.getLogger(__name__)


class VisionUnavailable(Exception):
    """No vision model is configured, or the call to it failed."""


_SYSTEM = (
    "You describe photographs attached to a personal journal entry. Write two or "
    "three plain sentences covering what is in the frame — people, place, objects, "
    "text that is legible, time of day. Be concrete and factual; do not speculate "
    "about how anyone feels, and do not editorialise about the photo's quality or "
    "composition. Return only the description."
)

_CHAT_SYSTEM = (
    "You are reading a photo on behalf of an assistant that cannot see images, so "
    "your description is the only thing it will ever know about this picture. Be "
    "concrete and factual. Name what is in the frame as specifically as you can — "
    "if it is food, name the dish, its components and roughly how much is there; "
    "if it is a product, name it. Quote any text that is legible — a menu, a "
    "label, a sign, packaging, a receipt — exactly as written, spelling included, "
    "because the assistant relies on it to get names right. Say plainly when "
    "something is unreadable or you are unsure rather than guessing at it. Do not "
    "speculate about how anyone feels and do not editorialise about the photo. "
    "Return only the description."
)

_MAX_TOKENS = 300

# More room than a journal caption: quoting a menu or an ingredient label
# verbatim is the point of this pass, and that is where the tokens go.
_CHAT_MAX_TOKENS = 500

# Matches backend/journal/storage.py's IMAGE_EXTS. heic/heif are in the list now
# that every image is re-encoded on the way out (see `data_uri`): Pillow opens
# them via the HEIF opener `backend.imaging` registers, and what reaches
# llama.cpp is a JPEG either way. Nothing here is sent as-is, so the values are
# gone — this is a "can we be expected to decode it" set, not a mime table.
_READABLE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}

# The longest side the vision tower is given. Measured on a 5712x4284 iPhone
# photo sent whole: 4104 image tokens, 110 s of prompt eval at 37 t/s, for a
# caption that gained nothing from the extra pixels — the projector runs on the
# CPU (`mmproj-offload = false`), so pixels are the cost driver and this is the
# single cheapest lever on captioning latency. 1280 keeps a menu or a street
# sign legible, which is what the chat-photo prompt is for.
_MAX_EDGE = 1280

# High enough that JPEG artefacts don't eat small text, low enough that the
# base64 payload stays a few hundred KB rather than a few MB.
_JPEG_QUALITY = 85


def get_vision_model() -> str | None:
    """The configured vision router alias, or None when captioning is off."""
    c = get_provider_config()
    return (c.get('llama_vision_model') or '').strip() or None


def is_vision_configured() -> bool:
    return get_vision_model() is not None


def data_uri(path: Path) -> str:
    """The `data:` URI an `image_url` content part needs: a JPEG, upright, with
    its longest side at most `_MAX_EDGE`.

    Two things this is not allowed to skip, both of which the stored file gets
    wrong for the model's purposes:

    - **Orientation.** A phone writes the sensor's pixels and an EXIF tag saying
      which way is up. llama.cpp's projector reads pixels and ignores the tag, so
      a portrait photo arrives on its side; the model notices, and says so — a
      real caption from this repo's own photos opened *"A person ... walks across
      a paved lot, with the image rotated 90 degrees clockwise."* Attention spent
      on that is attention not spent on the sign in the background.
    - **Size.** See `_MAX_EDGE`. Sending the original is minutes per photo.

    Exported (not `_`-prefixed) because `backend/chat/context.py` builds image
    parts for the chat model's own turn and must go through the same pass — two
    copies of this rule would drift, and the chat path has the same latency.
    """
    ext = path.suffix.lower().lstrip('.')
    if ext not in _READABLE_EXTS:
        raise VisionUnavailable(
            f'{ext or "this"} images cannot be read — convert to JPEG or PNG'
        )
    try:
        # Imported for the side effect: it registers Pillow's HEIF opener, which
        # is the only reason a .heic off an iPhone opens here at all.
        import backend.imaging  # noqa: F401
        from PIL import Image, ImageOps

        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((_MAX_EDGE, _MAX_EDGE))
            # A PNG or HEIC may carry alpha or a palette; JPEG takes neither.
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=_JPEG_QUALITY)
    except Exception as e:
        raise VisionUnavailable(f'The image could not be read: {e}') from e
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/jpeg;base64,{encoded}'


def _router_message(model: str, e: Exception) -> str:
    """Name the configured alias when llama-server is the one refusing it.

    The two router failures that matter are indistinguishable from a network
    error once they are `str(e)` on an attachment row: a 400/404 `model 'X' not
    found` (the alias has no section in `llama/presets.ini` — this is what a
    stale `gemma4-vision` looked like for months) and a 500 `failed to load`
    (the section exists but the weights would not fit; on one 8 GB card,
    `gemma4-12b-omni` beside a resident `[qwen36]` dies in llama.cpp's
    device-memory probe with `CUDA error: out of memory`). Both are settings
    problems and neither reads like one.
    """
    text = str(e) or 'Failed'
    lowered = text.lower()
    if 'not found' in lowered and model.lower() in lowered:
        return (
            f"llama-server has no model called '{model}' — pick one it lists in"
            ' Settings → llama.cpp, or add the preset to llama/presets.ini'
        )
    if 'failed to load' in lowered:
        return (
            f"llama-server could not load '{model}' (often no room beside the"
            f' chat model) — {text}'
        )
    return text


def describe_image(path: Path, *, system: str, prompt: str, max_tokens: int = _MAX_TOKENS) -> str:
    """Ask the vision model about the image at `path`, or raise VisionUnavailable.

    The one place in the app that sends an image anywhere. Callers supply their
    own system prompt because what a description is *for* differs: a journal
    caption is prose about a memory, while a chat photo is read for the facts in
    the frame that the conversation is about to need.
    """
    model = get_vision_model()
    if not model:
        raise VisionUnavailable(
            'No vision model configured — set one in Settings → llama.cpp'
        )
    if not path.is_file():
        raise VisionUnavailable('The image file is missing')

    try:
        client = get_llama_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': data_uri(path)}},
                ]},
            ],
            max_tokens=max_tokens,
            # Thinking off, explicitly. Gemma 4's chat template defaults it *on*,
            # and a caption is a 300-token budget: the reasoning consumes the
            # whole allowance and `content` comes back empty, which surfaces as
            # "The model returned an empty description" with nothing to suggest
            # the model saw the image perfectly well. Measured, not assumed —
            # the same request with thinking on returns '' and with it off
            # returns the caption. This call builds its own request rather than
            # going through backend/ai/llm.py, which is why it has to say so
            # itself; see `_request_kwargs` there for the same reasoning.
            extra_body={'chat_template_kwargs': {'enable_thinking': False}},
            timeout=600,
        )
        text = (resp.choices[0].message.content or '').strip()
    except VisionUnavailable:
        raise
    except Exception as e:
        logger.error('Image captioning failed: %s', e)
        raise VisionUnavailable(_router_message(model, e)) from e

    if not text:
        raise VisionUnavailable('The model returned an empty description')
    return text


def caption_image(path: Path, hint: str | None = None) -> str:
    """Describe a journal photo attachment.

    `hint` is the user's name for the attachment; it is passed as context so a
    photo labelled "the leak under the sink" gets described as such rather than
    as an anonymous close-up of a pipe.
    """
    prompt = 'Describe this photo.'
    if hint and hint.strip():
        prompt = f'Describe this photo. The person who saved it called it: "{hint.strip()}".'
    return describe_image(path, system=_SYSTEM, prompt=prompt)


def read_chat_photo(path: Path, hint: str | None = None) -> str:
    """Read a photo attached to a chat message, for the text-only chat model.

    Not the journal caption. The chat model never sees the picture, so this text
    *is* the picture as far as the conversation is concerned — and its most
    valuable job is transcribing legible text: a photographed menu, label or
    receipt routinely spells the exact proper noun speech-to-text just mangled.
    """
    prompt = 'Describe this photo.'
    if hint and hint.strip():
        prompt = (
            'Describe this photo. For context, the person sending it said: '
            f'"{hint.strip()}" — but that came from speech-to-text and may have '
            'misheard names, so trust the image over it.'
        )
    return describe_image(path, system=_CHAT_SYSTEM, prompt=prompt, max_tokens=_CHAT_MAX_TOKENS)
