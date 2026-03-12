#!/usr/bin/env python3
"""
Lunaschal STT + TTS Service
  POST /transcribe  — speech-to-text (faster-whisper)
  POST /tts         — text-to-speech  (kokoro-onnx)
  GET  /health      — readiness check

Environment variables:
  WHISPER_MODEL        Model name (default: large-v3-turbo)
  WHISPER_DEVICE       cuda or cpu (default: cuda)
  WHISPER_COMPUTE_TYPE Quantisation (default: int8_float16)
  TTS_VOICE            Kokoro voice (default: af_heart)
  STT_PORT             Port (default: 8765)
  STT_HOST             Bind address (default: 127.0.0.1)
"""

import io
import os
import tempfile
import logging
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from faster_whisper import WhisperModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MODEL_NAME    = os.environ.get("WHISPER_MODEL",        "large-v3-turbo")
DEVICE        = os.environ.get("WHISPER_DEVICE",        "cuda")
COMPUTE_TYPE  = os.environ.get("WHISPER_COMPUTE_TYPE",  "int8_float16")
TTS_VOICE     = os.environ.get("TTS_VOICE",             "af_heart")
PORT          = int(os.environ.get("STT_PORT",           "8765"))
HOST          = os.environ.get("STT_HOST",               "127.0.0.1")

# Kokoro ONNX model files — downloaded once to ~/.cache/lunaschal/tts/
_TTS_CACHE    = Path.home() / ".cache" / "lunaschal" / "tts"
_KOKORO_MODEL = "kokoro-v1.0.onnx"
_KOKORO_VOICES = "voices-v1.0.bin"
_KOKORO_BASE  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

stt_model: WhisperModel | None = None
tts_kokoro = None


def _download_tts_models() -> tuple[str, str]:
    """Download Kokoro model files if not already cached. Returns (model, voices) paths."""
    _TTS_CACHE.mkdir(parents=True, exist_ok=True)
    model_path  = _TTS_CACHE / _KOKORO_MODEL
    voices_path = _TTS_CACHE / _KOKORO_VOICES

    for path, filename in [(model_path, _KOKORO_MODEL), (voices_path, _KOKORO_VOICES)]:
        if not path.exists():
            url = f"{_KOKORO_BASE}/{filename}"
            logger.info("Downloading %s (~%s)…", filename,
                        "80 MB" if "onnx" in filename else "10 MB")
            urllib.request.urlretrieve(url, path)
            logger.info("Downloaded %s", filename)

    return str(model_path), str(voices_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global stt_model, tts_kokoro

    # --- Whisper STT ---
    logger.info("Loading %s on %s (%s)…", MODEL_NAME, DEVICE, COMPUTE_TYPE)
    logger.info("First run downloads the model (~1.5 GB) — please wait.")
    stt_model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    logger.info("STT model ready.")

    # Warmup: run one silent pass so CUDA kernels are compiled before the
    # first real request arrives.
    try:
        silence = np.zeros(16000, dtype=np.float32)  # 1 s of silence
        buf = io.BytesIO()
        sf.write(buf, silence, 16000, format="WAV")
        buf.seek(0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(buf.read())
            warmup_path = f.name
        list(stt_model.transcribe(warmup_path, vad_filter=True)[0])
        os.unlink(warmup_path)
        logger.info("STT warmup complete.")
    except Exception as e:
        logger.warning("STT warmup failed (non-fatal): %s", e)

    # --- Kokoro TTS ---
    try:
        from kokoro_onnx import Kokoro
        model_path, voices_path = _download_tts_models()
        tts_kokoro = Kokoro(model_path, voices_path)
        logger.info("TTS ready (Kokoro, voice=%s).", TTS_VOICE)

        # Warmup: one short synthesis to compile the ONNX session.
        tts_kokoro.create("Hello.", voice=TTS_VOICE, lang="en-us")
        logger.info("TTS warmup complete.")
    except Exception as e:
        logger.warning("TTS unavailable: %s", e)

    yield

    stt_model = None
    tts_kokoro = None


app = FastAPI(title="Lunaschal STT+TTS Service", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stt_model": MODEL_NAME,
        "stt_ready": stt_model is not None,
        "tts_ready": tts_kokoro is not None,
    }


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
):
    if stt_model is None:
        raise HTTPException(status_code=503, detail="STT model not loaded yet")

    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file")

    suffix = os.path.splitext(audio.filename or ".wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        segments, info = stt_model.transcribe(
            tmp_path,
            language=language or None,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            beam_size=5,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
        }
    except Exception as e:
        logger.error("Transcription error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)


@app.post("/tts")
async def tts(
    text: str = Form(...),
    voice: str = Form(TTS_VOICE),
    speed: float = Form(1.0),
):
    if tts_kokoro is None:
        raise HTTPException(status_code=503, detail="TTS not available")
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    try:
        samples, sample_rate = tts_kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    except Exception as e:
        logger.error("TTS error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return Response(content=buf.read(), media_type="audio/wav")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
