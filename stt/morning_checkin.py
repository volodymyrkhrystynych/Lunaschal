#!/usr/bin/env python3
"""
Lunaschal Morning Check-in Daemon

Monitors for wake-from-sleep events. When the computer wakes between
MORNING_START_HOUR and MORNING_END_HOUR, starts a voice conversation
that helps the user rubber-duck their plans for the day.

Run as background daemon: ./stt/run_morning_checkin.sh
Run immediately (test):   ./stt/run_morning_checkin.sh --now

Environment variables:
  STT_URL               (default: http://127.0.0.1:8765)
  LUNASCHAL_URL         (default: http://127.0.0.1:3000)
  MORNING_START_HOUR    start of check-in window, inclusive (default: 8)
  MORNING_END_HOUR      end of check-in window, exclusive  (default: 11)
"""

import datetime
import io
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Optional

import numpy as np
import requests
import scipy.io.wavfile as wavfile
import sounddevice as sd
import soundfile as sf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

STT_URL       = os.environ.get("STT_URL",             "http://127.0.0.1:8765")
LUNASCHAL_URL = os.environ.get("LUNASCHAL_URL",        "http://127.0.0.1:3000")
MORNING_START = int(os.environ.get("MORNING_START_HOUR", "8"))
MORNING_END   = int(os.environ.get("MORNING_END_HOUR",   "11"))

SAMPLE_RATE       = 16000
CHANNELS          = 1
SILENCE_THRESHOLD = 0.015  # RMS energy below this counts as silence
SILENCE_DURATION  = 1.5    # Seconds of silence before recording stops
MAX_RECORD_SECS   = 60     # Hard cap per turn

SYSTEM_PROMPT = (
    "You are a concise morning planning coach. "
    "Help the user clarify and think through their plans for the day "
    "with short, focused questions — like a rubber-duck session. "
    "Ask one question at a time. Keep every response under three sentences. "
    "After four or five exchanges, wrap up warmly and let them get to work."
)

DONE_PHRASES = {
    "done", "that's all", "bye", "goodbye", "that's it",
    "stop", "exit", "finish", "finished", "all done", "see you",
    "that's everything", "i'm good", "i'm done",
}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean_for_tts(text: str) -> str:
    text = re.sub(r'```[^\n]*\n?', '', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\*{1,3}([^*]*)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}([^_]*)_{1,3}', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\|', ' ', text)
    text = re.sub(r'[\U00010000-\U0010FFFF\U00002600-\U000027BF\U0001F000-\U0001FFFF]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Audio I/O
# ---------------------------------------------------------------------------

def speak(text: str) -> None:
    clean = _clean_for_tts(text)
    if not clean:
        return
    resp = requests.post(f"{STT_URL}/tts", data={"text": clean}, timeout=30)
    resp.raise_for_status()
    buf = io.BytesIO(resp.content)
    data, samplerate = sf.read(buf, dtype="float32")
    sd.play(data, samplerate)
    sd.wait()


def listen() -> str:
    """Record until natural speech pause, then return transcribed text."""
    audio_chunks: list[np.ndarray] = []
    stop_flag = threading.Event()

    def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        if not stop_flag.is_set():
            audio_chunks.append(indata.copy())

    print("\r🎤 Listening…                         ", end="", flush=True)

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32", callback=callback
    )
    stream.start()

    started_at = time.time()
    silence_at: Optional[float] = None
    has_speech = False

    while True:
        time.sleep(0.08)

        if time.time() - started_at > MAX_RECORD_SECS:
            break

        if len(audio_chunks) < 5:
            continue

        recent = np.concatenate(audio_chunks[-5:])
        energy = float(np.sqrt(np.mean(recent ** 2)))

        if energy > SILENCE_THRESHOLD:
            has_speech = True
            silence_at = None
        elif has_speech:
            if silence_at is None:
                silence_at = time.time()
            elif time.time() - silence_at >= SILENCE_DURATION:
                break  # Speech followed by silence — done

    stop_flag.set()
    stream.stop()
    stream.close()

    if not audio_chunks or not has_speech:
        return ""

    audio     = np.concatenate(audio_chunks, axis=0)
    audio_i16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf       = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, audio_i16)
    buf.seek(0)

    print("\r⏳ Transcribing…                      ", end="", flush=True)
    resp = requests.post(
        f"{STT_URL}/transcribe",
        files={"audio": ("recording.wav", buf, "audio/wav")},
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json().get("text", "").strip()
    print(f"\rYou: {text[:72]}", flush=True)
    return text


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def chat(history: list[dict]) -> str:
    messages = [m for m in history if m["role"] != "system"]
    resp = requests.post(
        f"{LUNASCHAL_URL}/api/chat/stream",
        json={"messages": messages},
        stream=True,
        timeout=60,
    )
    resp.raise_for_status()
    reply = ""
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            if "content" in chunk:
                reply += chunk["content"]
        except json.JSONDecodeError:
            pass
    return reply.strip()


# ---------------------------------------------------------------------------
# Check-in conversation
# ---------------------------------------------------------------------------

def _wait_for_services(timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{STT_URL}/health", timeout=3)
            d = r.json()
            if d.get("stt_ready", d.get("ready", False)) and d.get("tts_ready", False):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def run_checkin() -> None:
    logger.info("Morning check-in starting")

    if not _wait_for_services(timeout=60):
        logger.warning("Services not ready — skipping morning check-in")
        return

    now      = datetime.datetime.now()
    greeting = (
        f"Good morning! It's {now.strftime('%-I:%M %p')}. "
        "What are you planning to work on today?"
    )

    history: list[dict] = [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": greeting},
    ]

    print(f"\n{'─' * 50}")
    print(f"  Morning Check-in  {now.strftime('%H:%M')}")
    print(f"{'─' * 50}")
    print(f"Assistant: {greeting}")
    speak(greeting)

    missed = 0
    for turn in range(6):
        user_text = listen()

        if not user_text:
            missed += 1
            if missed >= 2:
                break
            speak("Take your time, I'm listening.")
            continue
        missed = 0

        if any(phrase in user_text.lower() for phrase in DONE_PHRASES):
            farewell = "Great, sounds like a solid plan. Have a productive day!"
            print(f"Assistant: {farewell}")
            speak(farewell)
            break

        history.append({"role": "user", "content": user_text})

        try:
            reply = chat(history)
        except Exception as e:
            logger.error("Chat error: %s", e)
            speak("Sorry, I ran into an issue. Good luck today!")
            break

        if not reply:
            break

        history.append({"role": "assistant", "content": reply})
        print(f"Assistant: {reply}")
        speak(reply)

        if turn == 5:
            wrap_up = "I'll let you get to it. Have a great day!"
            print(f"Assistant: {wrap_up}")
            speak(wrap_up)

    print(f"{'─' * 50}\n")
    logger.info("Morning check-in complete")


# ---------------------------------------------------------------------------
# Wake-from-sleep monitor
# ---------------------------------------------------------------------------

def _checkin_done_today() -> bool:
    """True if we already ran a check-in today (survives multiple wakes)."""
    flag  = os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "lunaschal_checkin_date"
    )
    today = datetime.date.today().isoformat()
    try:
        return open(flag).read().strip() == today
    except FileNotFoundError:
        return False


def _mark_checkin_done() -> None:
    flag  = os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "lunaschal_checkin_date"
    )
    today = datetime.date.today().isoformat()
    with open(flag, "w") as f:
        f.write(today)


def _in_morning_window() -> bool:
    hour = datetime.datetime.now().hour
    return MORNING_START <= hour < MORNING_END


def monitor_loop() -> None:
    logger.info(
        "Wake monitor running — check-in window %d:00–%d:00",
        MORNING_START, MORNING_END,
    )
    last_tick = time.time()

    while True:
        time.sleep(10)
        now     = time.time()
        elapsed = now - last_tick
        last_tick = now

        # Wall-clock jump > 30 s while we were "sleeping" for 10 s
        # means the system was suspended and just resumed.
        if elapsed <= 30:
            continue

        logger.info("Wake from sleep detected (gap: %.0f s)", elapsed)

        if not _in_morning_window():
            logger.info("Outside morning window (%d:00–%d:00), skipping", MORNING_START, MORNING_END)
            continue

        if _checkin_done_today():
            logger.info("Check-in already done today, skipping")
            continue

        _mark_checkin_done()

        # Give audio and network services a moment to settle after wake
        logger.info("Waiting 8 s for services to settle…")
        time.sleep(8)

        try:
            run_checkin()
        except Exception as e:
            logger.error("Check-in failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--now" in sys.argv:
        run_checkin()
    else:
        monitor_loop()
