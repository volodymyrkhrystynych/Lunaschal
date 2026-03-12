#!/usr/bin/env python3
"""
Lunaschal STT Service — faster-whisper based transcription server.
Runs on http://127.0.0.1:8765 by default.

Environment variables:
  WHISPER_MODEL        Model name (default: large-v3-turbo)
  WHISPER_DEVICE       cuda or cpu (default: cuda)
  WHISPER_COMPUTE_TYPE Quantisation (default: int8_float16)
  STT_PORT             Port (default: 8765)
  STT_HOST             Bind address (default: 127.0.0.1)
"""

import io
import os
import tempfile
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8_float16")
PORT = int(os.environ.get("STT_PORT", "8765"))
HOST = os.environ.get("STT_HOST", "127.0.0.1")

model: WhisperModel | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    logger.info(f"Loading {MODEL_NAME} on {DEVICE} ({COMPUTE_TYPE})...")
    logger.info("First run downloads the model (~1.5 GB) — please wait.")
    model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
    logger.info("Model ready.")
    yield
    model = None


app = FastAPI(title="Lunaschal STT Service", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "ready": model is not None}


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file")

    suffix = os.path.splitext(audio.filename or ".wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(
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
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
