import io
import os
import tempfile
import logging
import threading
import urllib.request
from pathlib import Path

from backend.ai.chat import chat_stream
from backend.ai.provider import is_ai_configured

import soundfile as sf
from flask import Blueprint, Response, jsonify, request
from backend.db.connection import get_db

logger = logging.getLogger(__name__)

bp = Blueprint('stt', __name__)

# Env-var defaults — used as fallback when no DB setting exists
STT_BACKEND      = os.environ.get('STT_BACKEND', 'local').lower()
TTS_BACKEND      = os.environ.get('TTS_BACKEND', 'local').lower()
MODEL_NAME       = os.environ.get('WHISPER_MODEL', 'turbo')
DEVICE           = os.environ.get('WHISPER_DEVICE', 'cuda')
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
_transcribe_lock    = threading.Lock()   # serialises Whisper inference — model is not thread-safe
_tts_lock           = threading.Lock()
_openai_lock        = threading.Lock()
_stt_model          = None
_tts_kokoro         = None
_openai_client      = None
_stt_ready          = False
_tts_ready          = False
_loaded_stt_backend = None   # 'local' or 'openai'
_loaded_model_name  = None   # whisper model name when _loaded_stt_backend == 'local'
_loaded_tts_backend = None   # 'local' or 'openai'
_loaded_device      = None   # 'cuda' or 'cpu'

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


def _load_stt(model_name: str | None = None, backend: str | None = None):
    global _stt_model, _stt_ready, _loaded_stt_backend, _loaded_model_name, _loaded_device
    backend = backend or _get_active_stt_backend()
    model_name = model_name or _get_active_whisper_model()

    # Fast path — already loaded with the right config
    if _stt_ready and _loaded_stt_backend == backend:
        if backend == 'openai' or _loaded_model_name == model_name:
            return

    with _stt_lock:
        if _stt_ready and _loaded_stt_backend == backend:
            if backend == 'openai' or _loaded_model_name == model_name:
                return
        # Unload whatever is currently in memory
        _stt_model = None
        _stt_ready = False

        if backend == 'openai':
            _ensure_openai()
            _loaded_stt_backend = 'openai'
            _loaded_model_name = None
            _loaded_device = None
        else:
            import whisper
            logger.info("Loading Whisper '%s' on %s…", model_name, DEVICE)
            _stt_model = whisper.load_model(model_name, device=DEVICE)
            _loaded_device = DEVICE
            _loaded_stt_backend = 'local'
            _loaded_model_name = model_name
            logger.info("STT ready.")
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
    return jsonify({'success': True})


@bp.get('/api/stt/health')
def stt_health():
    active_stt = _get_active_stt_backend()
    active_model = _get_active_whisper_model()
    active_tts = _get_active_tts_backend()
    stt_is_ready = _stt_ready and _loaded_stt_backend == active_stt and (
        active_stt == 'openai' or _loaded_model_name == active_model
    )
    return jsonify({
        'status': 'ok',
        'stt_backend': active_stt,
        'stt_model': active_model if active_stt == 'local' else f'openai/{OPENAI_STT_MODEL}',
        'stt_device': _loaded_device or DEVICE,
        'stt_ready': stt_is_ready,
        'tts_backend': active_tts,
        'tts_ready': _tts_ready and _loaded_tts_backend == active_tts,
    })


def _reset_stt_model() -> None:
    """Unload the Whisper model so it is reloaded fresh on the next request."""
    global _stt_model, _stt_ready, _loaded_stt_backend, _loaded_model_name, _loaded_device
    with _stt_lock:
        _stt_model = None
        _stt_ready = False
        _loaded_stt_backend = None
        _loaded_model_name = None
        _loaded_device = None


def _do_transcribe(content: bytes, filename: str, language: str | None) -> dict:
    """Transcribe audio bytes; returns {'text': str, 'language': str}."""
    if len(content) < 1000:
        raise ValueError('Audio too short or empty')

    suffix = os.path.splitext(filename or '.wav')[1] or '.wav'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        if _loaded_stt_backend == 'openai':
            with open(tmp_path, 'rb') as f:
                result = _openai_client.audio.transcriptions.create(
                    model=OPENAI_STT_MODEL,
                    file=(filename or f'audio{suffix}', f),
                    language=language,
                    response_format='verbose_json',
                )
            return {'text': result.text.strip(), 'language': result.language or language or 'en'}
        else:
            opts = {'language': language} if language else {}
            with _transcribe_lock:
                try:
                    result = _stt_model.transcribe(tmp_path, **opts)
                    return {'text': result['text'].strip(), 'language': result.get('language', language or 'en')}
                except Exception:
                    # Reset so the next request reloads with a fresh CUDA context
                    _reset_stt_model()
                    raise
    finally:
        os.unlink(tmp_path)


@bp.post('/api/transcribe')
def transcribe():
    if 'audio' not in request.files:
        return jsonify({'error': 'Missing audio file'}), 400
    audio_file = request.files['audio']
    content = audio_file.read()
    if not content:
        return jsonify({'error': 'Empty audio file'}), 400
    language = request.form.get('language') or None

    active_backend = _get_active_stt_backend()
    active_model = _get_active_whisper_model()
    try:
        _load_stt(active_model, active_backend)
    except Exception as e:
        return jsonify({'error': str(e)}), 503

    try:
        result = _do_transcribe(content, audio_file.filename or '', language)
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
    """Transcribe an audio file then use the LLM to fix errors against a ground-truth document."""
    if 'audio' not in request.files:
        return jsonify({'error': 'Missing audio file'}), 400
    audio_file = request.files['audio']
    content = audio_file.read()
    if not content:
        return jsonify({'error': 'Empty audio file'}), 400

    ground_truth = (request.form.get('ground_truth') or '').strip()
    language = request.form.get('language') or None

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

    if not ground_truth or not is_ai_configured():
        return jsonify({'raw': raw_text, 'corrected': raw_text, 'language': stt_result['language']})

    system_prompt = (
        'You are a transcription corrector. '
        'You will be given a raw speech-to-text transcription and a ground truth reference document. '
        'Correct any errors in the transcription — wrong words, misheared proper nouns, domain-specific terms — '
        'so that it matches terminology in the reference. '
        'Return only the corrected transcription text, preserving the original meaning and structure. '
        'Do not add commentary or explanations.'
    )
    user_message = (
        f'Ground truth reference document:\n---\n{ground_truth}\n---\n\n'
        f'Raw transcription:\n{raw_text}\n\nCorrected transcription:'
    )

    try:
        corrected = ''.join(chat_stream(
            [{'role': 'user', 'content': user_message}],
            system_prompt=system_prompt,
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
