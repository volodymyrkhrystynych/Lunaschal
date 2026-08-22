import io
import os
import re
import tempfile
import logging
import threading
import time
import urllib.request
import warnings
from pathlib import Path

from ulid import ULID

from backend.ai.chat import chat_stream
from backend.ai.provider import is_ai_configured
from backend.ai.transcribe_polish import polish_transcript

import soundfile as sf
from flask import Blueprint, Response, jsonify, request
from backend.db.connection import get_db

logger = logging.getLogger(__name__)

bp = Blueprint('stt', __name__)

# Env-var defaults — used as fallback when no DB setting exists
#
# `parakeet` is the default backend, and Whisper defaults to CPU, because
# llama-server now holds most of the 8 GB card for as long as it runs (unlike
# Ollama, which released VRAM after keep_alive). A CUDA Whisper alongside it
# means an OOM for whichever loads second — and losing a recording to a failed
# transcription is worse than a slower one. Both are still selectable in
# Settings for anyone who unloads the model first.
STT_BACKEND      = os.environ.get('STT_BACKEND', 'parakeet').lower()
TTS_BACKEND      = os.environ.get('TTS_BACKEND', 'local').lower()
MODEL_NAME       = os.environ.get('WHISPER_MODEL', 'turbo')
DEVICE           = os.environ.get('WHISPER_DEVICE', 'cpu')
# Parakeet TDT runs on CPU via onnx-asr (onnxruntime). English-only, no VRAM.
PARAKEET_MODEL   = os.environ.get('PARAKEET_MODEL', 'nemo-parakeet-tdt-0.6b-v2')
TTS_VOICE        = os.environ.get('TTS_VOICE', 'af_heart')
OPENAI_STT_MODEL = os.environ.get('OPENAI_STT_MODEL', 'whisper-1')
OPENAI_TTS_MODEL = os.environ.get('OPENAI_TTS_MODEL', 'tts-1')
OPENAI_TTS_VOICE = os.environ.get('OPENAI_TTS_VOICE', 'nova')

WHISPER_MODELS = [
    {'name': 'tiny',     'vramMb': 1024},
    {'name': 'base',     'vramMb': 1024},
    {'name': 'small',    'vramMb': 2048},
    {'name': 'medium',   'vramMb': 5120},
    {'name': 'turbo',    'vramMb': 6144},
    {'name': 'large-v3', 'vramMb': 10240},
]

_TTS_CACHE     = Path.home() / '.cache' / 'lunaschal' / 'tts'
_KOKORO_MODEL  = 'kokoro-v1.0.onnx'
_KOKORO_VOICES = 'voices-v1.0.bin'
_KOKORO_BASE   = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'

_stt_lock           = threading.Lock()
_transcribe_lock    = threading.Lock()   # serialises Whisper/Parakeet inference — models are not thread-safe
# Guards the draft-side handle cache below. Separate from _transcribe_lock on
# purpose: the whole point of that cache is that draft work never waits on, or
# is waited on by, an interactive dictation request.
_draft_handle_lock  = threading.Lock()
_tts_lock           = threading.Lock()
_openai_lock        = threading.Lock()
_stt_model          = None
_stt_vad            = None   # silero VAD for long-audio chunking (onnx-asr)
_tts_kokoro         = None
_openai_client      = None
_stt_ready          = False
_tts_ready          = False
_loaded_stt_backend = None   # 'local' or 'openai'
_loaded_model_name  = None   # whisper model name when _loaded_stt_backend == 'local'
_loaded_tts_backend = None   # 'local' or 'openai'
_loaded_device      = None   # 'cuda' or 'cpu'

# Local backends run over every clip, in order. The first of these that matches the
# user's configured default STT backend becomes the "primary" candidate (see
# _pick_primary) — the one whose raw text is kept as what was actually heard,
# and the one returned unmerged when the LLM pass is off or unavailable.
#
# Deliberately two, not three, for now. NVIDIA Canary 180M Flash was tried as
# a third opinion (onnx-asr, same VAD-chunked path as Parakeet) and reverted:
# its onnx-asr decoder (NemoConformerAED._decoding) is pure greedy argmax with
# no repetition penalty, generating up to a fixed 1024-token cap per chunk
# regardless of how much audio that chunk actually contains. On a real ~3.4
# minute draft it produced 1270 words against Parakeet/Whisper's ~190 for the
# same clip, and took 695s to do it (RTF 3.4 — slower than the recording
# itself) — a runaway-decode failure, not a slow-but-correct one. The next
# candidate worth trying is a Wav2Vec2 CTC model: CTC output length is capped
# by input frame count, so it's structurally immune to this specific failure
# mode, unlike any attention-decoder model (Canary, Whisper-style AED). No
# single canonical pre-converted onnx-asr checkpoint with a solid current WER
# number was settled on yet, which is why it isn't here either.
MULTI_BACKENDS = ('parakeet', 'local')

# Persistent STT handles for the multi-backend draft path, keyed by
# (backend, model_name, device). Kept for the life of the process: the draft
# pipeline runs every backend in DRAFT_BACKENDS on every clip, and rebuilding
# them each time cost ~5s of the ~13-18s a 30-60s draft takes — about a third,
# spent reloading models that were discarded minutes earlier.
#
# These are deliberately NOT the interactive singleton above. Sharing one model
# object across both paths would undo the two properties private handles were
# introduced for: a draft would evict whatever a live dictation had loaded, and
# two threads could enter a non-thread-safe model at once. A separate cache
# keeps both, and just stops throwing the result away.
#
# Cost is RAM only — every draft backend is CPU-resident (parakeet is CPU-only
# by construction, and WHISPER_DEVICE defaults to cpu because llama-server holds
# the card). Roughly 1.1 GB for the parakeet + whisper-small pair actually
# configured.
_draft_handles: dict[tuple, dict] = {}

# Listener process reports its recording state here so the frontend can mirror it
_listener_state: dict = {'recording': False, 'transcribing': False, 'mode': None}


def _get_active_stt_backend() -> str:
    try:
        s = get_db().execute('SELECT stt_backend FROM settings LIMIT 1').fetchone()
        if s and s['stt_backend']:
            return s['stt_backend'].lower()
    except Exception:
        pass
    return STT_BACKEND


def _get_active_tts_backend() -> str:
    try:
        s = get_db().execute('SELECT tts_backend FROM settings LIMIT 1').fetchone()
        if s and s['tts_backend']:
            return s['tts_backend'].lower()
    except Exception:
        pass
    return TTS_BACKEND


def _get_active_whisper_model() -> str:
    try:
        s = get_db().execute('SELECT whisper_model FROM settings LIMIT 1').fetchone()
        if s and s['whisper_model']:
            return s['whisper_model']
    except Exception:
        pass
    return MODEL_NAME


def _get_active_stt_device() -> str:
    try:
        s = get_db().execute('SELECT stt_device FROM settings LIMIT 1').fetchone()
        if s and s['stt_device']:
            return s['stt_device'].lower()
    except Exception:
        pass
    return DEVICE


def _get_transcribe_polish_enabled() -> bool:
    try:
        s = get_db().execute(
            'SELECT transcribe_polish_enabled FROM settings LIMIT 1'
        ).fetchone()
        if s and s['transcribe_polish_enabled'] is not None:
            return bool(s['transcribe_polish_enabled'])
    except Exception:
        pass
    return True


def _ensure_openai():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    with _openai_lock:
        if _openai_client is not None:
            return _openai_client
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError('OPENAI_API_KEY required for openai backend')
        from openai import OpenAI
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def _build_stt_backend(backend: str, model_name: str | None = None, device: str | None = None) -> dict:
    """Build a fresh, unshared STT handle for `backend`.

    Pure — never touches the module-level singleton (_stt_model,
    _loaded_stt_backend, …) that the interactive /api/transcribe path uses.
    `_load_stt` calls this and caches the result in that singleton;
    `run_multi_backend_transcribe` (backend/journal/voice_drafts.py's draft
    pipeline) calls it directly for a private, throwaway instance per backend,
    so a slow multi-model batch never evicts what a live dictation request has
    loaded, and never contends with it over `_transcribe_lock` either.

    Handle shape: {'backend', 'model', 'vad', 'model_name', 'device'} —
    unused fields are None for a given backend.
    """
    model_name = model_name or _get_active_whisper_model()
    device = device or _get_active_stt_device()

    if backend == 'openai':
        _ensure_openai()
        return {'backend': 'openai', 'model': None, 'vad': None, 'model_name': None, 'device': None}
    elif backend == 'parakeet':
        import onnx_asr
        model_id = PARAKEET_MODEL
        logger.info("Loading %s '%s' on CPU (onnx-asr)…", backend, model_id)
        model = onnx_asr.load_model(model_id)
        # Load Silero VAD for long-audio chunking. The transformer
        # self-attention in these models crashes on sequences > ~15 min; VAD
        # splits audio into 20-second speech windows before transcription.
        vad = onnx_asr.load_vad('silero')
        logger.info('STT ready (%s).', backend)
        return {'backend': backend, 'model': model, 'vad': vad, 'model_name': model_id, 'device': 'cpu'}
    else:
        import whisper
        logger.info("Loading Whisper '%s' on %s…", model_name, device)
        model = whisper.load_model(model_name, device=device)
        logger.info('STT ready.')
        return {'backend': 'local', 'model': model, 'vad': None, 'model_name': model_name, 'device': device}


def _load_stt(model_name: str | None = None, backend: str | None = None):
    global _stt_model, _stt_vad, _stt_ready, _loaded_stt_backend, _loaded_model_name, _loaded_device
    backend = backend or _get_active_stt_backend()
    model_name = model_name or _get_active_whisper_model()
    device = _get_active_stt_device()

    # openai (cloud) and parakeet (fixed CPU model) don't depend on the whisper
    # model/device knobs, so a backend match alone is enough for the fast path.
    config_free = backend in ('openai', 'parakeet')

    # Parakeet also needs its VAD loaded (silero) — if it's missing, fall through.
    vad_needed = backend == 'parakeet' and _stt_vad is None

    # Fast path — already loaded with the right config.
    # For config-free backends (openai, parakeet) we check backend match + VAD readiness.
    # For local (Whisper) we still need model + device to match.
    if _stt_ready and _loaded_stt_backend == backend:
        if config_free and not vad_needed or (
            not config_free and _loaded_model_name == model_name and _loaded_device == device
        ):
            return

    with _stt_lock:
        if _stt_ready and _loaded_stt_backend == backend:
            if config_free and not vad_needed or (
                not config_free and _loaded_model_name == model_name and _loaded_device == device
            ):
                return
        # Unload whatever is currently in memory
        _stt_model = None
        _stt_vad = None
        _stt_ready = False

        handle = _build_stt_backend(backend, model_name, device)
        _stt_model = handle['model']
        _stt_vad = handle['vad']
        _loaded_stt_backend = handle['backend']
        _loaded_model_name = handle['model_name']
        _loaded_device = handle['device']
        _stt_ready = True


def _load_tts(backend: str | None = None):
    global _tts_kokoro, _tts_ready, _loaded_tts_backend
    backend = backend or _get_active_tts_backend()

    if _tts_ready and _loaded_tts_backend == backend:
        return
    with _tts_lock:
        if _tts_ready and _loaded_tts_backend == backend:
            return
        _tts_kokoro = None
        _tts_ready = False
        if backend == 'openai':
            _ensure_openai()
        else:
            _TTS_CACHE.mkdir(parents=True, exist_ok=True)
            model_path  = _TTS_CACHE / _KOKORO_MODEL
            voices_path = _TTS_CACHE / _KOKORO_VOICES
            for path, name in [(model_path, _KOKORO_MODEL), (voices_path, _KOKORO_VOICES)]:
                if not path.exists():
                    logger.info('Downloading %s…', name)
                    urllib.request.urlretrieve(f'{_KOKORO_BASE}/{name}', path)
            from kokoro_onnx import Kokoro
            _tts_kokoro = Kokoro(str(model_path), str(voices_path))
            logger.info("TTS ready (Kokoro voice=%s).", TTS_VOICE)
        _loaded_tts_backend = backend
        _tts_ready = True


@bp.get('/api/stt/listener-state')
def get_listener_state():
    return jsonify(_listener_state)


@bp.post('/api/stt/listener-state')
def set_listener_state():
    global _listener_state
    body = request.json or {}
    _listener_state = {
        'recording':    bool(body.get('recording', False)),
        'transcribing': bool(body.get('transcribing', False)),
        'mode':         body.get('mode'),
    }
    return jsonify({'success': True})


@bp.get('/api/stt/whisper-models')
def get_whisper_models():
    return jsonify(WHISPER_MODELS)


@bp.post('/api/stt/reload')
def stt_reload():
    global _stt_model, _stt_ready, _loaded_stt_backend, _loaded_model_name
    with _stt_lock:
        _stt_model = None
        _stt_ready = False
        _loaded_stt_backend = None
        _loaded_model_name = None
    # The draft path holds its own long-lived handles, which a reload must
    # clear too — otherwise "reload" would drop only half the loaded models and
    # the next voice draft would still run on the previous backend/model.
    reset_draft_handles()
    return jsonify({'success': True})


@bp.get('/api/stt/health')
def stt_health():
    active_stt = _get_active_stt_backend()
    active_model = _get_active_whisper_model()
    active_tts = _get_active_tts_backend()
    if active_stt == 'openai':
        stt_is_ready = _stt_ready and _loaded_stt_backend == 'openai'
        stt_model_label = f'openai/{OPENAI_STT_MODEL}'
    elif active_stt == 'parakeet':
        stt_is_ready = _stt_ready and _loaded_stt_backend == 'parakeet'
        stt_model_label = PARAKEET_MODEL
    else:
        stt_is_ready = (
            _stt_ready and _loaded_stt_backend == 'local'
            and _loaded_model_name == active_model
        )
        stt_model_label = active_model
    return jsonify({
        'status': 'ok',
        'stt_backend': active_stt,
        'stt_model': stt_model_label,
        'stt_device': _loaded_device or DEVICE,
        'stt_ready': stt_is_ready,
        'tts_backend': active_tts,
        'tts_ready': _tts_ready and _loaded_tts_backend == active_tts,
    })


def _reset_stt_model() -> None:
    """Unload the STT model so it is reloaded fresh on the next request."""
    global _stt_model, _stt_vad, _stt_ready, _loaded_stt_backend, _loaded_model_name, _loaded_device
    with _stt_lock:
        _stt_model = None
        _stt_vad = None
        _stt_ready = False
        _loaded_stt_backend = None
        _loaded_model_name = None
        _loaded_device = None


def _decode_to_16k_mono(path: str):
    """Decode any audio file to a 16 kHz mono float32 numpy array via ffmpeg.

    onnx-asr reads audio through soundfile, which can't decode the webm the
    browser's MediaRecorder produces (only the listener sends WAV). ffmpeg —
    already a hard dependency for meetings — normalises every input format to
    the 16 kHz mono PCM Parakeet expects."""
    import subprocess
    import numpy as np

    proc = subprocess.run(
        ['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', path,
         '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _transcribe_with_handle(handle: dict, content: bytes, filename: str, language: str | None) -> dict:
    """Transcribe audio bytes with a handle from `_build_stt_backend`.

    Pure — reads no module globals, so a caller can safely run this
    concurrently with the interactive singleton path using its own private
    handle (see `run_multi_backend_transcribe`). Returns {'text', 'language'}.
    """
    if len(content) < 1000:
        raise ValueError('Audio too short or empty')

    suffix = os.path.splitext(filename or '.wav')[1] or '.wav'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        backend = handle['backend']
        if backend == 'openai':
            with open(tmp_path, 'rb') as f:
                result = _openai_client.audio.transcriptions.create(
                    model=OPENAI_STT_MODEL,
                    file=(filename or f'audio{suffix}', f),
                    language=language,
                    response_format='verbose_json',
                )
            return {'text': result.text.strip(), 'language': result.language or language or 'en'}
        elif backend == 'parakeet':
            # Parakeet is English-only and does no language detection. Long
            # audio (> ~15 min) crashes the transformer's self-attention, so
            # we use VAD chunking (20-second speech windows) via with_vad().
            waveform = _decode_to_16k_mono(tmp_path)
            vad_model = handle['model'].with_vad(handle['vad'])
            segments = list(vad_model.recognize(waveform, sample_rate=16000,
                                                 max_speech_duration_s=20))
            text = ' '.join(seg.text for seg in segments if seg.text.strip())
            return {'text': (text or '').strip(), 'language': language or 'en'}
        else:
            opts = {'language': language} if language else {}
            device = handle.get('device')
            if device == 'cpu':
                # CPU is a deliberate choice (Settings > STT Device), not an accidental
                # fallback — skip the fp16 probe and its "CUDA available but unused"
                # warning, which would otherwise fire on every single transcription.
                opts['fp16'] = False
            with warnings.catch_warnings():
                if device == 'cpu':
                    warnings.filterwarnings(
                        'ignore', message='Performing inference on CPU when CUDA is available')
                result = handle['model'].transcribe(tmp_path, **opts)
            return {'text': result['text'].strip(), 'language': result.get('language', language or 'en')}
    finally:
        os.unlink(tmp_path)


def _do_transcribe(content: bytes, filename: str, language: str | None) -> dict:
    """Transcribe audio bytes using whichever backend is currently loaded into
    the shared singleton (`_load_stt`); returns {'text': str, 'language': str}.

    Locks around parakeet/local — those model objects are shared
    across every interactive request and aren't thread-safe — and resets the
    singleton on failure so the next request reloads fresh (except on a
    too-short-audio ValueError, which isn't a model problem). openai isn't
    locked, matching its previous behaviour: it's a stateless HTTP client, not
    a loaded model.
    """
    handle = {
        'backend': _loaded_stt_backend,
        'model': _stt_model,
        'vad': _stt_vad,
        'model_name': _loaded_model_name,
        'device': _loaded_device,
    }
    if _loaded_stt_backend == 'openai':
        return _transcribe_with_handle(handle, content, filename, language)

    with _transcribe_lock:
        try:
            return _transcribe_with_handle(handle, content, filename, language)
        except ValueError:
            raise
        except Exception:
            _reset_stt_model()
            raise


def _draft_handle(backend: str) -> dict:
    """A cached, process-lifetime STT handle for the draft path.

    Built once per (backend, model_name, device) and kept. A settings change —
    a different Whisper model or device — lands under a new key rather than
    mutating the old entry, and the superseded key is evicted from the cache so
    only one handle per backend stays resident. Eviction is from the dict only:
    a draft already mid-transcription holds its own reference, so it finishes on
    the handle it started with instead of having the model pulled out from under
    it.
    """
    key = (backend, _get_active_whisper_model(), _get_active_stt_device())
    with _draft_handle_lock:
        handle = _draft_handles.get(key)
        if handle is None:
            handle = _build_stt_backend(backend, key[1], key[2])
            # Drop any handle for this backend under a stale config; only the
            # current key stays resident.
            for stale in [k for k in _draft_handles if k[0] == backend and k != key]:
                del _draft_handles[stale]
            _draft_handles[key] = handle
    return handle


def reset_draft_handles() -> None:
    """Drop every cached draft handle. For tests, and for /api/stt/reload — a
    user who switches STT backend or Whisper model expects the next draft to
    honour it without waiting for a process restart."""
    with _draft_handle_lock:
        _draft_handles.clear()


def run_multi_backend_transcribe(
    content: bytes, filename: str, language: str | None, backends: list[str],
) -> list[dict]:
    """Transcribe the same clip with each of `backends`, in turn, each using a
    handle private to this path rather than the shared singleton — so this can
    run fully alongside a live interactive request instead of evicting whatever
    it has loaded. Used by backend/journal/voice_drafts.py's draft pipeline.

    The handles are cached for the life of the process (see _draft_handles), so
    only the first draft after a restart pays the model load. They stay private
    to this path: reusing the interactive singleton instead would reintroduce
    both the eviction and the thread-safety problem that separate handles exist
    to avoid.

    One backend failing doesn't stop the others. Each result is
    {'backend': ..., 'text': ...} on success or {'backend': ..., 'error': ...}
    on failure. A backend that fails also has its handle dropped, so a model
    left in a bad state is rebuilt next time rather than being reused forever —
    the same reasoning as _do_transcribe's _reset_stt_model().
    """
    results = []
    for backend in backends:
        try:
            handle = _draft_handle(backend)
            result = _transcribe_with_handle(handle, content, filename, language)
            results.append({'backend': backend, 'text': result['text']})
        except Exception as e:
            logger.warning('Draft transcription failed for backend %s: %s', backend, e)
            # A ValueError is about the audio (too short/empty), not the model —
            # that handle is still good and worth keeping.
            if not isinstance(e, ValueError):
                with _draft_handle_lock:
                    for stale in [k for k in _draft_handles if k[0] == backend]:
                        del _draft_handles[stale]
            results.append({'backend': backend, 'error': str(e)})
    return results


def pick_primary(candidates: list[dict], active_backend: str) -> dict:
    """The candidate treated as what was actually heard: the user's configured
    backend if it produced text, else Parakeet, else whatever succeeded.

    It matters in two places — it is the transcript kept as journal
    raw_content, and it is what a caller gets back when the LLM merge is off or
    unavailable. Falling back to Parakeet rather than list order is deliberate:
    it is the app's default backend, so it is the one whose output the settings
    were most likely tuned against.
    """
    for c in candidates:
        if c['backend'] == active_backend:
            return c
    for c in candidates:
        if c['backend'] == 'parakeet':
            return c
    return candidates[0]


def transcribe_multi(content: bytes, filename: str, language: str | None) -> dict:
    """Transcribe one clip with every backend in MULTI_BACKENDS and reconcile
    the results into a single transcript.

    The shared implementation behind every dictation surface in the app: the
    three listener hotkeys and the twelve in-app microphone buttons all reach it
    through POST /api/transcribe, and journal attachment transcription calls it
    directly. Journal voice drafts run the same two backends through
    `run_multi_backend_transcribe` but merge with Journal's own prompt, which
    reformats into paragraphs — right for an entry, wrong for dictating a phrase
    into a text field, which is why this path uses `merge_transcripts` instead.

    Returns {'text', 'raw_text', 'language', 'candidates'}: `text` is the
    reconciled transcript, `raw_text` the primary candidate before merging.
    Raises RuntimeError only if *every* backend failed — one surviving backend
    is a normal, usable result, since the merge degrades to polishing it.
    """
    from backend.ai.transcribe_polish import merge_transcripts

    results = run_multi_backend_transcribe(content, filename, language, list(MULTI_BACKENDS))
    candidates = [r for r in results if (r.get('text') or '').strip()]
    if not candidates:
        summary = '; '.join(
            f"{r['backend']}: {r.get('error', 'no speech detected')}" for r in results
        ) or 'All STT backends failed'
        raise RuntimeError(summary)

    primary = pick_primary(candidates, _get_active_stt_backend())
    # Primary first: merge_transcripts falls back to texts[0] whenever the LLM
    # is unreachable, so the candidate we'd have picked anyway must be the one
    # it falls back to.
    ordered = [primary['text']] + [c['text'] for c in candidates if c is not primary]
    return {
        'text': merge_transcripts(ordered),
        'raw_text': primary['text'],
        'language': language or 'en',
        'candidates': results,
    }


# Sources whose transcriptions are kept in the transcription log (Journal view,
# hidden behind a toggle). Journal-key transcriptions already persist as journal
# entries; voice mode is conversational and intentionally not logged.
_LOGGED_SOURCES = {'paste'}

# Window classes treated as browsers: their page title is stored as extra
# context (it names the site/page). All other apps store the class only —
# titles elsewhere (documents, chats) are more sensitive than useful.
_BROWSER_CLASSES = (
    'firefox', 'librewolf', 'zen', 'vivaldi', 'chromium', 'google-chrome',
    'brave', 'opera', 'qutebrowser', 'epiphany',
)

_BROWSER_TITLE_SUFFIX = re.compile(
    r'\s+[-—–]\s+(Mozilla Firefox|LibreWolf|Zen Browser|Vivaldi|Chromium|'
    r'Google Chrome|Brave|Opera|qutebrowser|Web)$',
    re.IGNORECASE,
)


def _window_detail(app: str, title: str) -> str | None:
    if not title or not any(app.startswith(b) for b in _BROWSER_CLASSES):
        return None
    return _BROWSER_TITLE_SUFFIX.sub('', title).strip() or None


def _log_transcription(result: dict, form) -> None:
    source = (form.get('source') or '').strip().lower()
    if source not in _LOGGED_SOURCES or not result.get('text'):
        return
    try:
        app = (form.get('app') or '').strip().lower() or None
        detail = _window_detail(app, (form.get('window_title') or '').strip()) if app else None
        db = get_db()
        db.execute(
            'INSERT INTO transcriptions (id, text, source, app, detail, created_at)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (str(ULID()), result['text'], source, app, detail, int(time.time())),
        )
        db.commit()
    except Exception:
        # Logging must never break the transcription response
        logger.exception('Failed to log transcription')


@bp.post('/api/transcribe')
def transcribe():
    if 'audio' not in request.files:
        return jsonify({'error': 'Missing audio file'}), 400
    audio_file = request.files['audio']
    content = audio_file.read()
    if not content:
        return jsonify({'error': 'Empty audio file'}), 400
    language = request.form.get('language') or None

    # The "Polish transcriptions with AI" setting now switches the whole
    # transcription strategy, not just a cleanup pass on the end of it. On, a
    # clip goes through every backend in MULTI_BACKENDS and the results are
    # cross-checked by the LLM; off, it takes the single configured backend and
    # nothing else. Running both models and then *not* reconciling them would
    # be the worst of both — twice the CPU for a transcript no better than the
    # primary one — so the choice is made here rather than at the merge.
    if _get_transcribe_polish_enabled():
        try:
            result = transcribe_multi(content, audio_file.filename or '', language)
        except ValueError as e:
            logger.warning('Transcription skipped: %s', e)
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error('Transcription error: %s', e)
            return jsonify({'error': str(e)}), 500
        _log_transcription(result, request.form)
        return jsonify({
            'text': result['text'],
            'language': result['language'],
            'language_probability': 1.0,
        })

    active_backend = _get_active_stt_backend()
    active_model = _get_active_whisper_model()
    try:
        _load_stt(active_model, active_backend)
    except Exception as e:
        return jsonify({'error': str(e)}), 503

    try:
        result = _do_transcribe(content, audio_file.filename or '', language)
        _log_transcription(result, request.form)
        return jsonify({**result, 'language_probability': 1.0})
    except ValueError as e:
        # Short/empty audio — not a model error, don't reset
        logger.warning('Transcription skipped: %s', e)
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error('Transcription error: %s', e)
        # Model was reset; next request will reload it
        return jsonify({'error': str(e)}), 500


@bp.post('/api/transcribe/correct')
def transcribe_correct():
    """Transcribe an audio file then use the LLM to fix errors against
    reference material: the standing memory document (backend/memory.py,
    always consulted) plus an optional pasted-in ground-truth document."""
    if 'audio' not in request.files:
        return jsonify({'error': 'Missing audio file'}), 400
    audio_file = request.files['audio']
    content = audio_file.read()
    if not content:
        return jsonify({'error': 'Empty audio file'}), 400

    ground_truth = (request.form.get('ground_truth') or '').strip()
    language = request.form.get('language') or None

    # Same switch as /api/transcribe: on, every backend runs and the results
    # are reconciled before the ground-truth correction below sees them. The
    # two LLM passes do different jobs and both are worth having here — the
    # merge decides what was said, the correction below decides what the
    # speaker's own vocabulary calls it.
    if _get_transcribe_polish_enabled():
        try:
            multi = transcribe_multi(content, audio_file.filename or '', language)
        except Exception as e:
            logger.error('Transcription error: %s', e)
            return jsonify({'error': str(e)}), 500
        # `raw` is what the Editor's STT panel shows beside the correction, so
        # it stays the unreconciled primary transcript — showing the merged text
        # as "raw" would hide half of what this panel exists to let you compare.
        raw_text = multi['raw_text']
        stt_result = {'text': multi['text'], 'language': multi['language']}
        corrected_input = multi['text']
    else:
        active_backend = _get_active_stt_backend()
        active_model = _get_active_whisper_model()
        try:
            _load_stt(active_model, active_backend)
        except Exception as e:
            return jsonify({'error': str(e)}), 503

        try:
            stt_result = _do_transcribe(content, audio_file.filename or '', language)
        except Exception as e:
            logger.error('Transcription error: %s', e)
            return jsonify({'error': str(e)}), 500

        raw_text = stt_result['text']
        corrected_input = raw_text

    from backend.memory import get_memory
    memory = get_memory().strip()
    reference = '\n\n'.join(
        part for part in (
            f'Things known about the speaker:\n{memory}' if memory else '',
            f'Ground truth reference document:\n{ground_truth}' if ground_truth else '',
        ) if part
    )

    if not reference or not is_ai_configured():
        return jsonify({'raw': raw_text, 'corrected': raw_text, 'language': stt_result['language']})

    system_prompt = (
        'You are a transcription corrector. '
        'You will be given a raw speech-to-text transcription and reference material. '
        'Correct any errors in the transcription — wrong words, misheared proper nouns, domain-specific terms — '
        'so that it matches terminology in the reference. '
        'Only change a word when the reference gives you a specific reason to. '
        'Return only the corrected transcription text, preserving the original meaning and structure. '
        'Do not add commentary or explanations.'
    )
    user_message = (
        f'Reference material:\n---\n{reference}\n---\n\n'
        f'Raw transcription:\n{corrected_input}\n\nCorrected transcription:'
    )

    try:
        corrected = ''.join(chat_stream(
            [{'role': 'user', 'content': user_message}],
            system_prompt=system_prompt,
            with_time_context=False,
        )).strip()
    except Exception as e:
        logger.error('LLM correction error: %s', e)
        return jsonify({'error': f'Correction failed: {e}'}), 500

    return jsonify({'raw': raw_text, 'corrected': corrected, 'language': stt_result['language']})


@bp.post('/api/tts')
def tts():
    text = request.form.get('text', '').strip()
    if not text:
        return jsonify({'error': 'Empty text'}), 400
    voice = request.form.get('voice')
    speed = float(request.form.get('speed', 1.0))

    active_backend = _get_active_tts_backend()

    try:
        _load_tts(active_backend)
    except Exception as e:
        return jsonify({'error': str(e)}), 503

    try:
        if active_backend == 'openai':
            audio = _openai_client.audio.speech.create(
                model=OPENAI_TTS_MODEL,
                voice=voice or OPENAI_TTS_VOICE,
                input=text,
                response_format='wav',
                speed=max(0.25, min(4.0, speed)),
            )
            return Response(audio.content, content_type='audio/wav')
        else:
            samples, sample_rate = _tts_kokoro.create(
                text, voice=voice or TTS_VOICE, speed=speed, lang='en-us',
            )
            buf = io.BytesIO()
            sf.write(buf, samples, sample_rate, format='WAV')
            buf.seek(0)
            return Response(buf.read(), content_type='audio/wav')
    except Exception as e:
        logger.error('TTS error: %s', e)
        return jsonify({'error': str(e)}), 500
