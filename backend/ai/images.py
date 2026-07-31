"""Image captioning for journal photo attachments.

This is the project's only vision call, and it is deliberately gated behind its
own model alias (`llama_vision_model`) rather than reusing `llama_model`:

Both presets in `llama/presets.ini` set `mmproj-auto = false`, which skips
Gemma 4's ~1.1 GB vision tower. That is not an oversight — the 26B preset already
sits at 5604 MiB on a 7.8 GB card with ~870 MiB of headroom, so loading the
projector would not fit alongside it (docs/learnings/moe-expert-placement.md).
Captioning therefore stays off until a vision-capable alias is configured, and
`VisionUnavailable` is what the UI shows instead of a mysteriously dead button.

To enable it, add a preset with the projector attached, e.g.

    [gemma4-vision]
    model       = /home/volodya/.cache/llama.cpp/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf
    mmproj      = /home/volodya/.cache/llama.cpp/mmproj-gemma-4-26B-A4B-it-f16.gguf
    ctx-size    = 16384
    n-gpu-layers = 999
    n-cpu-moe   = 30

then set it in Settings → llama.cpp. Note that the router loading it will evict
the chat model, so the first caption after a chat costs a model load.
"""
import base64
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

_MAX_TOKENS = 300

# Matches backend/journal/storage.py's IMAGE_EXTS, mapped back to the mime types
# a data: URI needs. heic/heif are stored but not sent — llama.cpp's projector
# path decodes the common web formats, and a HEIC would arrive as garbage bytes.
_EXT_MIME = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'webp': 'image/webp',
    'gif': 'image/gif',
}


def get_vision_model() -> str | None:
    """The configured vision router alias, or None when captioning is off."""
    c = get_provider_config()
    return (c.get('llama_vision_model') or '').strip() or None


def is_vision_configured() -> bool:
    return get_vision_model() is not None


def _data_uri(path: Path) -> str:
    ext = path.suffix.lower().lstrip('.')
    mime = _EXT_MIME.get(ext)
    if not mime:
        raise VisionUnavailable(
            f'{ext or "this"} images cannot be captioned — convert to JPEG or PNG'
        )
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'data:{mime};base64,{encoded}'


def caption_image(path: Path, hint: str | None = None) -> str:
    """Describe the image at `path`, or raise VisionUnavailable.

    `hint` is the user's name for the attachment; it is passed as context so a
    photo labelled "the leak under the sink" gets described as such rather than
    as an anonymous close-up of a pipe.
    """
    model = get_vision_model()
    if not model:
        raise VisionUnavailable(
            'No vision model configured — set one in Settings → llama.cpp'
        )
    if not path.is_file():
        raise VisionUnavailable('The image file is missing')

    prompt = 'Describe this photo.'
    if hint and hint.strip():
        prompt = f'Describe this photo. The person who saved it called it: "{hint.strip()}".'

    try:
        client = get_llama_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': _data_uri(path)}},
                ]},
            ],
            max_tokens=_MAX_TOKENS,
            timeout=600,
        )
        text = (resp.choices[0].message.content or '').strip()
    except VisionUnavailable:
        raise
    except Exception as e:
        logger.error('Image captioning failed: %s', e)
        raise VisionUnavailable(str(e)) from e

    if not text:
        raise VisionUnavailable('The model returned an empty description')
    return text
