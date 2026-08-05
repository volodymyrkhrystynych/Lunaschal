"""Non-speech audio/video description for journal attachments.

This is a separate call from transcription on purpose. Parakeet/Whisper (see
backend/routes/stt.py) answer "what was said"; this answers "what else is
going on" — a dog barking, a door slamming, music playing — the ambient
content a word-for-word transcript throws away.

It is also gated behind its own model alias (`llama_audio_model`), the same
pattern as backend/ai/images.py's `llama_vision_model`, and for a stronger
reason than VRAM headroom: audio input is a capability of the Gemma 4
E2B/E4B/12B "encoder-free" variants, not the 26B A4B MoE model this router
already loads for chat. There is no shared-weights trick here — describing
audio means loading genuinely different weights, so this stays off until a
`[gemma4-e4b-audio]`-style preset is downloaded and configured (see the
comment in llama/presets.ini). `AudioUnavailable` is what the UI shows instead
of a mysteriously dead button.
"""
import base64
import logging
import subprocess
from pathlib import Path

from backend.ai.provider import get_llama_client, get_provider_config

logger = logging.getLogger(__name__)


class AudioUnavailable(Exception):
    """No audio-description model is configured, or the call to it failed."""


_SYSTEM = (
    "You describe the audio track of a personal journal attachment. Write two "
    "or three plain sentences covering the non-speech content — background "
    "sounds, music, tone of voice, notable noises — and only briefly summarise "
    "any speech rather than transcribing it verbatim. Be concrete and factual; "
    "do not speculate about how anyone feels. Return only the description."
)

_MAX_TOKENS = 300


def get_audio_model() -> str | None:
    """The configured audio-description router alias, or None when it's off."""
    c = get_provider_config()
    return (c.get('llama_audio_model') or '').strip() or None


def is_audio_configured() -> bool:
    return get_audio_model() is not None


def _wav_data_uri(path: Path) -> str:
    """Transcode any container to 16 kHz mono WAV via ffmpeg, same reasoning as
    stt.py's _decode_to_16k_mono: the model's audio encoder needs a format it
    can decode, and the source may be the webm a browser recorder produces."""
    try:
        proc = subprocess.run(
            ['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', str(path),
             '-ac', '1', '-ar', '16000', '-f', 'wav', '-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise AudioUnavailable('Could not read the audio track') from e
    encoded = base64.b64encode(proc.stdout).decode('ascii')
    return encoded


def describe_audio(path: Path, hint: str | None = None) -> str:
    """Describe the non-speech audio content at `path`, or raise AudioUnavailable.

    `hint` is the user's name for the attachment, passed as context the same
    way caption_image uses it in backend/ai/images.py.
    """
    model = get_audio_model()
    if not model:
        raise AudioUnavailable(
            'No audio-description model configured — set one in Settings → llama.cpp'
        )
    if not path.is_file():
        raise AudioUnavailable('The recording is missing')

    prompt = 'Describe the audio in this recording.'
    if hint and hint.strip():
        prompt = f'Describe the audio in this recording. The person who saved it called it: "{hint.strip()}".'

    audio_b64 = _wav_data_uri(path)

    try:
        client = get_llama_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'input_audio', 'input_audio': {'data': audio_b64, 'format': 'wav'}},
                ]},
            ],
            max_tokens=_MAX_TOKENS,
            timeout=600,
        )
        text = (resp.choices[0].message.content or '').strip()
    except AudioUnavailable:
        raise
    except Exception as e:
        logger.error('Audio description failed: %s', e)
        raise AudioUnavailable(str(e)) from e

    if not text:
        raise AudioUnavailable('The model returned an empty description')
    return text
