"""Non-speech audio/video description for journal attachments.

This is a separate call from transcription on purpose. Parakeet/Whisper (see
backend/routes/stt.py) answer "what was said"; this answers "what else is
going on" — a dog barking, a door slamming, music playing — the ambient
content a word-for-word transcript throws away.

It is also gated behind its own model alias (`llama_audio_model`), the same
pattern as backend/ai/images.py's `llama_vision_model`, and for a stronger
reason than VRAM headroom: audio input is a capability of the Gemma 4
"encoder-free" variants, not of the text-only MoE this router loads for chat.
There is no shared-weights trick here — describing audio means loading
genuinely different weights, so this stays off until `[gemma4-12b-omni]` is
downloaded and configured (see the comment in llama/presets.ini).
`AudioUnavailable` is what the UI shows instead of a mysteriously dead button.

That preset is any-to-any, so it is the same model backend/ai/images.py uses
for photos, and Settings sets both aliases with one checkbox. The two columns
stay separate because the two features fail independently.

**A long recording is described in windows, not in one call.** The audio
encoder emits a token per 160 ms frame, so a recording costs ~6.25 tokens per
second of wall time regardless of what is in it — a two-hour walk is ~54,000
tokens against the preset's 16,384, which llama-server answers with a flat 400.
Duration, not content, is what overflows the context, so the fix is temporal:
slice, describe each slice, then hand the slice descriptions to the *chat*
model to be worked into the two or three sentences an attachment actually
shows. Same map-reduce shape backend/meetings/pipeline.py already uses to keep
a long track from becoming one impossible call.
"""
import base64
import logging
import math
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

_REDUCE_SYSTEM = (
    "You are given timestamped notes describing consecutive excerpts of one "
    "recording, written by someone listening to each excerpt in isolation. "
    "Combine them into two or three plain sentences describing the recording as "
    "a whole: what the setting sounds like, what changes over its length, and "
    "anything notable that happens. Do not list the excerpts, do not repeat "
    "timestamps, and do not invent anything the notes do not mention. Be "
    "concrete and factual. Return only the description."
)

_MAX_TOKENS = 300

# --- Window sizing -----------------------------------------------------------
#
# `ctx-size` for [gemma4-12b-omni] in llama/presets.ini. There is no way to ask
# llama-server for it at runtime (see backend/ai/llm.py: the context window
# belongs to the server, not the request), so it is mirrored here — the two have
# to move together, and _MAX_WINDOW_SECONDS below is what makes the consequence
# of the number visible rather than implicit.
_CONTEXT_TOKENS = 16384
# One token per 160 ms frame. A property of Gemma 4's audio encoder, not of the
# recording — silence costs exactly what speech costs.
_AUDIO_TOKENS_PER_SECOND = 6.25
# The description we ask for, plus room for the system prompt and the hint.
_RESERVED_TOKENS = _MAX_TOKENS + 400
# ~42 minutes. The hard ceiling a single call cannot exceed.
_MAX_WINDOW_SECONDS = int((_CONTEXT_TOKENS - _RESERVED_TOKENS) / _AUDIO_TOKENS_PER_SECOND)

# What we actually slice at, deliberately far below that ceiling for two
# reasons. The preset is `n-gpu-layers = 0`, so prefill is CPU-bound and scales
# with window length — a near-full-context window would push one call toward the
# timeout below. And two or three sentences summarising forty minutes of audio
# would be worthless anyway; ten minutes is a granularity that can still say
# something specific. The cost is that a 12-minute clip gets sliced when it
# would have fit whole, which is a reduce call we could have skipped and a
# better description than one call would have produced.
_WINDOW_SECONDS = 600

# A compute budget, not a context limit: each window is a separate CPU
# generation of a minute or more, so a three-hour video would otherwise occupy
# the background worker for most of an hour. Past this, windows are spread
# evenly across the whole recording instead of covering its first two hours —
# truncation would silently describe the beginning of a long recording as if it
# were the whole thing, and the end is usually the part worth hearing about.
_MAX_WINDOWS = 12

_TIMEOUT = 600


def get_audio_model() -> str | None:
    """The configured audio-description router alias, or None when it's off."""
    c = get_provider_config()
    return (c.get('llama_audio_model') or '').strip() or None


def is_audio_configured() -> bool:
    return get_audio_model() is not None


def _probe_duration(path: Path) -> float | None:
    """Length of `path` in seconds, or None if ffprobe can't say.

    Same invocation backend/meetings/storage.py uses. A failure here is not
    fatal: it costs the windowing (we fall back to one whole-file call, which is
    what this module did before windows existed), not the description.
    """
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', str(path)],
            check=True, capture_output=True, text=True,
        )
        duration = float(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        logger.warning('Could not probe the duration of %s: %s', path, e)
        return None
    return duration if duration > 0 else None


def plan_windows(duration: float) -> list[tuple[float, float]]:
    """`(start, length)` pairs, in seconds, to describe a `duration`-long file.

    Pure, so the whole policy can be walked in a test rather than inferred from
    ffmpeg invocations. Returns a single whole-file window for anything short
    enough to fit; contiguous windows up to `_MAX_WINDOWS`; and beyond that,
    `_MAX_WINDOWS` windows spread evenly from the start of the recording to its
    end, so a sampled description still covers how the recording finishes.
    """
    if duration <= _WINDOW_SECONDS:
        return [(0.0, duration)]

    count = math.ceil(duration / _WINDOW_SECONDS)
    if count <= _MAX_WINDOWS:
        return [(i * _WINDOW_SECONDS,
                 min(_WINDOW_SECONDS, duration - i * _WINDOW_SECONDS))
                for i in range(count)]

    # Evenly spaced, first window flush with the start and last flush with the
    # end — the alternative (centring the samples) drops the opening and closing
    # of the recording, which are the two moments most likely to matter.
    stride = (duration - _WINDOW_SECONDS) / (_MAX_WINDOWS - 1)
    return [(i * stride, float(_WINDOW_SECONDS)) for i in range(_MAX_WINDOWS)]


def _clock(seconds: float) -> str:
    """`m:ss` under an hour, `h:mm:ss` over it."""
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def _wav_b64(path: Path, start: float | None = None, length: float | None = None) -> str:
    """Transcode any container to 16 kHz mono WAV via ffmpeg, same reasoning as
    stt.py's _decode_to_16k_mono: the model's audio encoder needs a format it
    can decode, and the source may be the webm a browser recorder produces.

    With `start`/`length`, decodes only that window. `-ss` before `-i` seeks
    rather than decoding-and-discarding, which is what keeps slicing a
    three-hour file into windows from costing three hours of decode per window.
    """
    seek = ['-ss', f'{start:.3f}'] if start else []
    trim = ['-t', f'{length:.3f}'] if length is not None else []
    try:
        proc = subprocess.run(
            ['ffmpeg', '-nostdin', '-loglevel', 'error', *seek, '-i', str(path),
             *trim, '-ac', '1', '-ar', '16000', '-f', 'wav', '-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise AudioUnavailable('Could not read the audio track') from e
    return base64.b64encode(proc.stdout).decode('ascii')


def _hint_suffix(hint: str | None) -> str:
    if hint and hint.strip():
        return f' The person who saved it called it: "{hint.strip()}".'
    return ''


def _describe_one(client, model: str, audio_b64: str, prompt: str) -> str:
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
        # Thinking off, explicitly — same reason as backend/ai/images.py, and
        # worse here: _MAX_TOKENS is 300 per window, so reasoning would eat the
        # budget of every window in a long recording and `_reduce` would be
        # handed nothing to summarise. Gemma 4's template defaults it on, and
        # this call bypasses backend/ai/llm.py's `_request_kwargs`, so it has to
        # set it here.
        extra_body={'chat_template_kwargs': {'enable_thinking': False}},
        timeout=_TIMEOUT,
    )
    return (resp.choices[0].message.content or '').strip()


def _reduce(notes: list[str], hint: str | None) -> str:
    """Work the per-window descriptions into one short description.

    Deliberately the *chat* model (backend/ai/llm.py's default alias), not the
    audio one: this step is plain text, and the E4B audio model is the smaller,
    CPU-only, worse writer of the two — it is loaded for its encoder, not its
    prose. Raises on failure so the caller can fall back to the notes; a lumpy
    timestamped list is worth far more than an error on a recording we just
    spent ten minutes listening to.
    """
    from backend.ai.llm import chat_text

    body = '\n'.join(notes)
    prompt = f'Notes on the excerpts:\n\n{body}'
    if hint and hint.strip():
        prompt = f'The person who saved this recording called it: "{hint.strip()}".\n\n{prompt}'
    text = (chat_text(prompt, system=_REDUCE_SYSTEM) or '').strip()
    if not text:
        raise AudioUnavailable('The model returned an empty description')
    return text


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

    client = get_llama_client()
    duration = _probe_duration(path)
    windows = plan_windows(duration) if duration is not None else [(0.0, None)]

    if len(windows) == 1:
        try:
            text = _describe_one(
                client, model, _wav_b64(path),
                f'Describe the audio in this recording.{_hint_suffix(hint)}',
            )
        except AudioUnavailable:
            raise
        except Exception as e:
            logger.error('Audio description failed: %s', e)
            raise AudioUnavailable(str(e)) from e
        if not text:
            raise AudioUnavailable('The model returned an empty description')
        return text

    total = _clock(duration)
    notes: list[str] = []
    last_error: Exception | None = None
    for start, length in windows:
        stamp = f'{_clock(start)}–{_clock(start + length)}'
        prompt = (
            f'This is the excerpt from {stamp} of a {total} recording.'
            f' Describe the audio in this excerpt.{_hint_suffix(hint)}'
        )
        try:
            text = _describe_one(client, model, _wav_b64(path, start, length), prompt)
        except Exception as e:
            # One unreadable window shouldn't cost the other eleven — a
            # description built from most of a recording is still a description.
            logger.warning('Audio description failed for window %s of %s: %s', stamp, path, e)
            last_error = e
            continue
        if text:
            notes.append(f'[{stamp}] {text}')

    if not notes:
        logger.error('Audio description failed for every window of %s', path)
        raise AudioUnavailable(str(last_error) if last_error else 'The model returned an empty description')

    covered = sum(length for _, length in windows)
    sampled = covered < duration - 1

    try:
        description = _reduce(notes, hint)
    except Exception as e:
        # The notes ARE the description at this point, just longer and blockier
        # than the two sentences we asked for. Losing them to a failed summary
        # would throw away the entire expensive half of the work.
        logger.warning('Could not summarise the audio description of %s: %s', path, e)
        description = '\n'.join(notes)

    if sampled:
        # Said here rather than asked of the model, because it is a fact about
        # what we listened to and has to be true. A description that quietly
        # covers 2 of 3 hours reads as a description of the whole recording.
        description += (
            f'\n\n(Sampled from {len(windows)} excerpts of'
            f' {int(_WINDOW_SECONDS // 60)} minutes spread across the {total} recording.)'
        )
    return description
