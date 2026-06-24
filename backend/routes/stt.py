import io
import os
import logging
import tempfile
import threading
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('stt', __name__)

STT_BACKEND = os.environ.get('STT_BACKEND', 'local').lower()
TTS_BACKEND = os.environ.get('TTS_BACKEND', 'local').lower()

WHISPER_MODEL   = os.environ.get('WHISPER_MODEL',        'large-v3-turbo')
WHISPER_DEVICE  = os.environ.get('WHISPER_DEVICE',       'cuda')
WHISPER_COMPUTE = os.environ.get('WHISPER_COMPUTE_TYPE', 'int8_float16')

TTS_VOICE = os.environ.get('TTS_VOICE', 'af_heart')

OPENAI_STT_MODEL = os.environ.get('OPENAI_STT_MODEL', 'whisper-1')
OPENAI_TTS_MODEL = os.environ.get('OPENAI_TTS_MODEL', 'tts-1')
OPENAI_TTS_VOICE = os.environ.get('OPENAI_TTS_VOICE', 'nova')

_TTS_CACHE     = Path.home() / '.cache' / 'lunaschal' / 'tts'
_KOKORO_MODEL  = 'kokoro-v1.0.onnx'
_KOKORO_VOICES = 'voices-v1.0.bin'
_KOKORO_BASE   = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'

_stt_model     = None
_tts_kokoro    = None
_openai_client = None
_stt_ready     = False
_tts_ready     = False
_init_lock     = threading.Lock()


def _download_tts_models():
    _TTS_CACHE.mkdir(parents=True, exist_ok=True)
    model_path  = _TTS_CACHE / _KOKORO_MODEL
    voices_path = _TTS_CACHE / _KOKORO_VOICES
    for path, filename in [(model_path, _KOKORO_MODEL), (voices_path, _KOKORO_VOICES)]:
        if not path.exists():
            logger.info('Downloading %s…', filename)
            urllib.request.urlretrieve(f'{_KOKORO_BASE}/{filename}', path)
    return str(model_path), str(voices_path)


def _init_models():
    global _stt_model, _tts_kokoro, _openai_client, _stt_ready, _tts_ready

    with _init_lock:
        if STT_BACKEND == 'openai' or TTS_BACKEND == 'openai':
            api_key = os.environ.get('OPENAI_API_KEY')
            if api_key:
                from openai import OpenAI
                _openai_client = OpenAI(api_key=api_key)
                logger.info('OpenAI client ready.')
            else:
                logger.warning('OPENAI_API_KEY not set — OpenAI STT/TTS unavailable')

        if STT_BACKEND == 'local':
            try:
                logger.info('Loading Whisper %s on %s (%s)…', WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE)
                from faster_whisper import WhisperModel
                _stt_model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
                # Warmup: compile CUDA kernels before the first real request
                silence = np.zeros(16000, dtype=np.float32)
                buf = io.BytesIO()
                sf.write(buf, silence, 16000, format='WAV')
                buf.seek(0)
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    f.write(buf.read())
                    warmup_path = f.name
                list(_stt_model.transcribe(warmup_path, vad_filter=True)[0])
                os.unlink(warmup_path)
                _stt_ready = True
                logger.info('STT model ready.')
            except ImportError:
                logger.warning('faster-whisper not installed — local STT unavailable (pip install faster-whisper)')
            except Exception as e:
                logger.error('STT init failed: %s', e)
        elif STT_BACKEND == 'openai':
            _stt_ready = _openai_client is not None

        if TTS_BACKEND == 'local':
            try:
                from kokoro_onnx import Kokoro
                model_path, voices_path = _download_tts_models()
                _tts_kokoro = Kokoro(model_path, voices_path)
                _tts_kokoro.create('Hello.', voice=TTS_VOICE, lang='en-us')  # warmup
                _tts_ready = True
                logger.info('TTS ready (Kokoro, voice=%s).', TTS_VOICE)
            except ImportError:
                logger.warning('kokoro-onnx not installed — local TTS unavailable (pip install kokoro-onnx)')
            except Exception as e:
                logger.warning('TTS init failed: %s', e)
        elif TTS_BACKEND == 'openai':
            _tts_ready = _openai_client is not None


def start_init_thread():
    threading.Thread(target=_init_models, daemon=True, name='stt-init').start()


@bp.get('/api/stt/health')
def stt_health():
    return jsonify({
        'stt_backend': STT_BACKEND,
        'stt_model': f'openai/{OPENAI_STT_MODEL}' if STT_BACKEND == 'openai' else WHISPER_MODEL,
        'stt_ready': _stt_ready,
        'tts_backend': TTS_BACKEND,
        'tts_ready': _tts_ready,
    })


@bp.post('/api/transcribe')
def transcribe():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file'}), 400
    audio_file = request.files['audio']
    content = audio_file.read()
    if not content:
        return jsonify({'error': 'Empty audio file'}), 400
    language = request.form.get('language')

    if STT_BACKEND == 'openai':
        if _openai_client is None:
            return jsonify({'error': 'OpenAI client not initialized — set OPENAI_API_KEY'}), 503
        suffix = os.path.splitext(audio_file.filename or '.wav')[1] or '.wav'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            with open(tmp_path, 'rb') as f:
                result = _openai_client.audio.transcriptions.create(
                    model=OPENAI_STT_MODEL,
                    file=(audio_file.filename or f'audio{suffix}', f),
                    language=language or None,
                    response_format='verbose_json',
                )
            return jsonify({
                'text': result.text.strip(),
                'language': result.language or language or 'en',
                'language_probability': 1.0,
            })
        except Exception as e:
            logger.error('OpenAI transcription error: %s', e)
            return jsonify({'error': str(e)}), 500
        finally:
            os.unlink(tmp_path)
    else:
        if _stt_model is None:
            return jsonify({'error': 'STT model not loaded yet'}), 503
        suffix = os.path.splitext(audio_file.filename or '.wav')[1] or '.wav'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            segments, info = _stt_model.transcribe(
                tmp_path,
                language=language or None,
                vad_filter=True,
                vad_parameters={'min_silence_duration_ms': 300},
                beam_size=5,
            )
            text = ' '.join(seg.text.strip() for seg in segments).strip()
            return jsonify({
                'text': text,
                'language': info.language,
                'language_probability': round(info.language_probability, 3),
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

    if TTS_BACKEND == 'openai':
        if _openai_client is None:
            return jsonify({'error': 'OpenAI client not initialized'}), 503
        try:
            resp = _openai_client.audio.speech.create(
                model=OPENAI_TTS_MODEL,
                voice=voice or OPENAI_TTS_VOICE,
                input=text,
                response_format='wav',
                speed=max(0.25, min(4.0, speed)),
            )
            return Response(resp.content, mimetype='audio/wav')
        except Exception as e:
            logger.error('OpenAI TTS error: %s', e)
            return jsonify({'error': str(e)}), 500
    else:
        if _tts_kokoro is None:
            return jsonify({'error': 'TTS not available'}), 503
        try:
            samples, sample_rate = _tts_kokoro.create(text, voice=voice or TTS_VOICE, speed=speed, lang='en-us')
        except Exception as e:
            logger.error('TTS error: %s', e)
            return jsonify({'error': str(e)}), 500
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format='WAV')
        buf.seek(0)
        return Response(buf.read(), mimetype='audio/wav')
