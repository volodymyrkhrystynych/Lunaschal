import io
import os
import tempfile
import logging
import threading
import urllib.request
from pathlib import Path

import soundfile as sf
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('stt', __name__)

STT_BACKEND      = os.environ.get('STT_BACKEND', 'local').lower()
TTS_BACKEND      = os.environ.get('TTS_BACKEND', 'local').lower()
MODEL_NAME       = os.environ.get('WHISPER_MODEL', 'turbo')
DEVICE           = os.environ.get('WHISPER_DEVICE', 'cuda')
TTS_VOICE        = os.environ.get('TTS_VOICE', 'af_heart')
OPENAI_STT_MODEL = os.environ.get('OPENAI_STT_MODEL', 'whisper-1')
OPENAI_TTS_MODEL = os.environ.get('OPENAI_TTS_MODEL', 'tts-1')
OPENAI_TTS_VOICE = os.environ.get('OPENAI_TTS_VOICE', 'nova')

_TTS_CACHE     = Path.home() / '.cache' / 'lunaschal' / 'tts'
_KOKORO_MODEL  = 'kokoro-v1.0.onnx'
_KOKORO_VOICES = 'voices-v1.0.bin'
_KOKORO_BASE   = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'

_stt_lock      = threading.Lock()
_tts_lock      = threading.Lock()
_openai_lock   = threading.Lock()
_stt_model     = None
_tts_kokoro    = None
_openai_client = None
_stt_ready     = False
_tts_ready     = False


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


def _load_stt():
    global _stt_model, _stt_ready
    if _stt_ready:
        return
    with _stt_lock:
        if _stt_ready:
            return
        if STT_BACKEND == 'openai':
            _ensure_openai()
        else:
            import whisper
            logger.info("Loading Whisper '%s' on %s…", MODEL_NAME, DEVICE)
            _stt_model = whisper.load_model(MODEL_NAME, device=DEVICE)
            logger.info("STT ready.")
        _stt_ready = True


def _load_tts():
    global _tts_kokoro, _tts_ready
    if _tts_ready:
        return
    with _tts_lock:
        if _tts_ready:
            return
        if TTS_BACKEND == 'openai':
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
        _tts_ready = True


@bp.get('/api/stt/health')
def stt_health():
    return jsonify({
        'status': 'ok',
        'stt_backend': STT_BACKEND,
        'stt_model': MODEL_NAME if STT_BACKEND == 'local' else f'openai/{OPENAI_STT_MODEL}',
        'stt_ready': _stt_ready,
        'tts_backend': TTS_BACKEND,
        'tts_ready': _tts_ready,
    })


@bp.post('/api/transcribe')
def transcribe():
    if 'audio' not in request.files:
        return jsonify({'error': 'Missing audio file'}), 400
    audio_file = request.files['audio']
    content = audio_file.read()
    if not content:
        return jsonify({'error': 'Empty audio file'}), 400
    language = request.form.get('language') or None
    suffix = os.path.splitext(audio_file.filename or '.wav')[1] or '.wav'

    try:
        _load_stt()
    except Exception as e:
        return jsonify({'error': str(e)}), 503

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if STT_BACKEND == 'openai':
            with open(tmp_path, 'rb') as f:
                result = _openai_client.audio.transcriptions.create(
                    model=OPENAI_STT_MODEL,
                    file=(audio_file.filename or f'audio{suffix}', f),
                    language=language,
                    response_format='verbose_json',
                )
            return jsonify({
                'text': result.text.strip(),
                'language': result.language or language or 'en',
                'language_probability': 1.0,
            })
        else:
            opts = {}
            if language:
                opts['language'] = language
            result = _stt_model.transcribe(tmp_path, **opts)
            return jsonify({
                'text': result['text'].strip(),
                'language': result.get('language', language or 'en'),
                'language_probability': 1.0,
            })
    except Exception as e:
        logger.error('Transcription error: %s', e)
        return jsonify({'error': str(e)}), 500
    finally:
        os.unlink(tmp_path)


@bp.post('/api/tts')
def tts():
    text = request.form.get('text', '').strip()
    if not text:
        return jsonify({'error': 'Empty text'}), 400
    voice = request.form.get('voice')
    speed = float(request.form.get('speed', 1.0))

    try:
        _load_tts()
    except Exception as e:
        return jsonify({'error': str(e)}), 503

    try:
        if TTS_BACKEND == 'openai':
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
