#!/usr/bin/env python3
"""
Lunaschal Voice Input Listener

  Right Ctrl  — record → transcribe → paste text at cursor
  Right Alt   — record → transcribe → AI chat → speak reply

Uses evdev (reads /dev/input/event* directly) so both shortcuts work globally
on Wayland regardless of which window has focus.

Requirements:
  - User must be in the 'input' group:
      sudo usermod -a -G input $USER   (then log out/in or: newgrp input)
  - STT service running: ./stt/run_service.sh
  - wtype installed: sudo pacman -S wtype

Environment variables:
  STT_URL        STT/TTS service  (default: http://127.0.0.1:8765)
  LUNASCHAL_URL  Lunaschal server (default: http://127.0.0.1:3000)
"""

import io
import json
import os
import re
import sys
import time
import logging
import subprocess
import threading
from typing import Optional

import evdev
from evdev import InputDevice, categorize, ecodes

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

STT_URL       = os.environ.get("STT_URL",       "http://127.0.0.1:8765")
LUNASCHAL_URL = os.environ.get("LUNASCHAL_URL", "http://127.0.0.1:7842")

SAMPLE_RATE     = 16000
CHANNELS        = 1
TOGGLE_COOLDOWN = 0.5      # seconds — prevents double-fire on key hold
KEY_RELEASE_TIMEOUT = 3.0  # seconds to wait for trigger key release before typing

# Wake word detection (openwakeword)
WAKE_WORD_MODEL     = os.environ.get("WAKE_WORD_MODEL", "")      # path to .onnx model file
WAKE_WORD_THRESHOLD = float(os.environ.get("WAKE_WORD_THRESHOLD", "0.5"))
WAKE_SILENCE_RMS    = float(os.environ.get("WAKE_SILENCE_RMS",   "0.015"))  # energy threshold
WAKE_SILENCE_SECS   = float(os.environ.get("WAKE_SILENCE_SECS",  "1.5"))    # silence → auto-stop
WAKE_MIN_SPEECH_SECS = 0.5   # minimum speech before checking for silence
WAKEWORD_CHUNK      = 1280   # 80 ms at 16 kHz (openwakeword requirement)

# --- Voice assistant conversation history (in-memory) ---
_voice_history: list[dict] = [
    {
        "role": "system",
        "content": (
            "You are a Socratic thinking partner. "
            "Your only job is to ask one short, open-ended question that helps the user "
            "think more clearly about what they just said. "
            "Never give advice, explanations, or answers — only questions. "
            "Keep each question to one sentence. "
            "Your reply will be spoken aloud, so no markdown."
        ),
    }
]

# --- Recording state ---
_recording      = False
_current_mode   = "paste"   # "paste" | "voice"
_stream: Optional[sd.InputStream] = None
_audio_chunks: list[np.ndarray]   = []
_last_toggle    = 0.0
_wake_triggered = False      # True when the current recording was started by wake word

# Key-release events (so we wait for the trigger key to be up before typing/speaking)
_ctrl_released = threading.Event()
_ctrl_released.set()
_alt_released  = threading.Event()
_alt_released.set()


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _status(msg: str) -> None:
    print(f"\r{msg:<72}", end="", flush=True)


# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------

def _audio_callback(indata: np.ndarray, frames: int, time_info, status) -> None:
    if status:
        logger.debug("Audio callback: %s", status)
    _audio_chunks.append(indata.copy())


def _start_recording(mode: str) -> None:
    global _recording, _stream, _audio_chunks, _current_mode
    _current_mode = mode
    _audio_chunks = []
    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        callback=_audio_callback,
    )
    _stream.start()
    _recording = True
    icon = "🎙️ " if mode == "paste" else "🎤"
    stop_key = "Right Ctrl" if mode == "paste" else "Right Alt"
    _status(f"{icon} Recording… ({stop_key} to stop)")
    logger.info("Recording started (mode=%s)", mode)


# ---------------------------------------------------------------------------
# Paste mode — transcribe → type at cursor
# ---------------------------------------------------------------------------

def _transcribe_and_paste() -> None:
    global _recording, _stream

    audio = _stop_audio()
    if audio is None:
        return

    duration = len(audio) / SAMPLE_RATE
    _status(f"⏳ Transcribing {duration:.1f}s…")

    text = _transcribe(audio)
    if not text:
        return

    preview = text[:65] + ("…" if len(text) > 65 else "")
    _status(f"✓ {preview}")
    logger.info('Transcribed: "%s"', text)

    # Wait for Right Ctrl to be released so the compositor doesn't
    # apply Ctrl as a modifier to the pasted characters.
    _ctrl_released.wait(timeout=KEY_RELEASE_TIMEOUT)
    _paste_text(text)


def _paste_text(text: str) -> None:
    """Paste via clipboard + Ctrl+V (works in Electron apps too)."""
    proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))
    time.sleep(0.05)
    try:
        subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=True)
    except FileNotFoundError:
        _status("✓ Copied to clipboard — paste with Ctrl+V  (install wtype to auto-paste)")
    except subprocess.CalledProcessError as e:
        logger.error("wtype error: %s", e)


# ---------------------------------------------------------------------------
# Voice assistant mode — transcribe → AI chat → TTS → play
# ---------------------------------------------------------------------------

def _transcribe_and_chat() -> None:
    audio = _stop_audio()
    if audio is None:
        return

    duration = len(audio) / SAMPLE_RATE
    _status(f"⏳ Transcribing {duration:.1f}s…")

    text = _transcribe(audio)
    if not text:
        return

    _status(f"💬 You: {text[:60]}")
    logger.info('Voice input: "%s"', text)

    # Wait for Right Alt to be released before speaking (skip for wake-word triggers)
    if not _wake_triggered:
        _alt_released.wait(timeout=KEY_RELEASE_TIMEOUT)

    _voice_history.append({"role": "user", "content": text})
    reply = _chat(text)
    if not reply:
        _voice_history.pop()
        return

    _voice_history.append({"role": "assistant", "content": reply})

    preview = reply[:60] + ("…" if len(reply) > 60 else "")
    _status(f"🔊 {preview}")
    logger.info('AI reply: "%s"', reply[:120])

    _speak(_clean_for_tts(reply))


def _chat(user_text: str) -> str | None:
    """Send conversation history to Lunaschal's streaming chat endpoint, return full reply."""
    # Separate system message and send it as a dedicated field
    system_msg = next((m["content"] for m in _voice_history if m["role"] == "system"), None)
    messages = [m for m in _voice_history if m["role"] != "system"]
    payload: dict = {"messages": messages}
    if system_msg:
        payload["systemPrompt"] = system_msg
    try:
        with requests.post(
            f"{LUNASCHAL_URL}/api/chat/stream",
            json=payload,
            stream=True,
            timeout=60,
        ) as resp:
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
                    elif "error" in chunk:
                        logger.error("Chat API error: %s", chunk["error"])
                        _status(f"✗ Chat error: {chunk['error']}")
                        return None
                except json.JSONDecodeError:
                    pass
            return reply.strip() or None
    except requests.exceptions.ConnectionError:
        _status(f"✗ Lunaschal server unreachable at {LUNASCHAL_URL}")
        logger.error("Cannot connect to %s", LUNASCHAL_URL)
        return None
    except Exception as e:
        _status(f"✗ Chat error: {e}")
        logger.error("Chat error: %s", e)
        return None


def _clean_for_tts(text: str) -> str:
    """Remove markdown formatting and emojis so TTS reads clean prose."""
    # Code blocks (``` ... ```) — drop the fences, keep the content
    text = re.sub(r'```[^\n]*\n?', '', text)
    # Inline code
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # Bold/italic: ***x***, **x**, *x*, ___x___, __x__, _x_
    text = re.sub(r'\*{1,3}([^*]*)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}([^_]*)_{1,3}', r'\1', text)
    # ATX headings: # ## ###
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # Unordered list markers (-, *, +) at line start
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Links: [text](url) → text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Images: ![alt](url) → alt
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Table pipes and separator rows
    text = re.sub(r'\|', ' ', text)
    text = re.sub(r'^[\s:-]+$', '', text, flags=re.MULTILINE)
    # Emojis and pictographic symbols
    text = re.sub(r'[\U00010000-\U0010FFFF\U00002600-\U000027BF\U0001F000-\U0001FFFF]', '', text)
    # Collapse multiple blank lines into one
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _speak(text: str) -> None:
    """Send text to the TTS endpoint and play the returned audio."""
    try:
        resp = requests.post(
            f"{STT_URL}/tts",
            data={"text": text},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        _status(f"✗ STT/TTS service unreachable at {STT_URL}")
        return
    except Exception as e:
        _status(f"✗ TTS error: {e}")
        logger.error("TTS error: %s", e)
        return

    buf = io.BytesIO(resp.content)
    data, samplerate = sf.read(buf, dtype="float32")
    sd.play(data, samplerate)
    sd.wait()


# ---------------------------------------------------------------------------
# Shared audio helpers
# ---------------------------------------------------------------------------

def _stop_audio() -> np.ndarray | None:
    global _recording, _stream
    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None
    _recording = False

    if not _audio_chunks:
        _status("✗ No audio captured")
        return None

    audio = np.concatenate(_audio_chunks, axis=0)
    logger.info("Stopped recording — %.1fs captured", len(audio) / SAMPLE_RATE)
    return audio


def _transcribe(audio: np.ndarray) -> str | None:
    audio_i16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, audio_i16)
    buf.seek(0)

    try:
        resp = requests.post(
            f"{STT_URL}/transcribe",
            files={"audio": ("recording.wav", buf, "audio/wav")},
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
    except requests.exceptions.ConnectionError:
        _status(f"✗ STT service unreachable at {STT_URL}")
        logger.error("Cannot connect to %s", STT_URL)
        return None
    except Exception as e:
        _status(f"✗ STT error: {e}")
        logger.error("STT error: %s", e)
        return None

    if not text:
        _status("✗ No speech detected")
        return None

    return text


# ---------------------------------------------------------------------------
# Wake word detection (openwakeword)
# ---------------------------------------------------------------------------

def _wake_record_and_chat() -> None:
    """Record audio until silence, then process as a voice chat turn."""
    global _recording, _audio_chunks, _current_mode, _wake_triggered

    _audio_chunks = []
    _current_mode = "voice"
    _wake_triggered = True
    _recording = True

    min_frames = int(WAKE_MIN_SPEECH_SECS * SAMPLE_RATE)
    silence_frames = 0
    total_frames = 0

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32", blocksize=1024
        ) as stream:
            _status("🎤 Listening… (stops on silence)")
            while True:
                chunk, _ = stream.read(1024)
                flat = chunk.flatten()
                _audio_chunks.append(flat.reshape(-1, 1))
                total_frames += len(flat)

                if total_frames > min_frames:
                    energy = float(np.abs(flat).mean())
                    if energy < WAKE_SILENCE_RMS:
                        silence_frames += len(flat)
                        if silence_frames / SAMPLE_RATE >= WAKE_SILENCE_SECS:
                            break
                    else:
                        silence_frames = 0
    finally:
        _recording = False
        _wake_triggered = False

    threading.Thread(target=_transcribe_and_chat, daemon=True).start()


def _wake_word_loop() -> None:
    """Background thread: listen for the wake word, then trigger voice mode."""
    if not WAKE_WORD_MODEL:
        logger.info("WAKE_WORD_MODEL not set — wake word detection disabled")
        return

    try:
        from openwakeword.model import Model as _OWWModel  # type: ignore
        oww = _OWWModel(wakeword_models=[WAKE_WORD_MODEL], inference_framework="onnx")
        model_name = os.path.splitext(os.path.basename(WAKE_WORD_MODEL))[0]
        logger.info("Wake word model loaded: %s (threshold=%.2f)", model_name, WAKE_WORD_THRESHOLD)
        print(f"  Wake word  : \"Hey Luna\"  [{model_name}, threshold={WAKE_WORD_THRESHOLD}]\n")
    except Exception as e:
        logger.error("Cannot load wake word model '%s': %s", WAKE_WORD_MODEL, e)
        return

    while True:
        if _recording:
            time.sleep(0.1)
            continue

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=WAKEWORD_CHUNK
            ) as stream:
                oww.reset()
                while not _recording:
                    chunk, _ = stream.read(WAKEWORD_CHUNK)
                    prediction = oww.predict(chunk.flatten())
                    score = prediction.get(model_name, max(prediction.values(), default=0))
                    if score >= WAKE_WORD_THRESHOLD:
                        logger.info("Wake word detected (score=%.2f)", score)
                        # Brief pause so the wake phrase itself isn't captured
                        time.sleep(0.25)
                        _wake_record_and_chat()
                        break
        except Exception as e:
            logger.error("Wake word loop error: %s", e)
            time.sleep(1)


# ---------------------------------------------------------------------------
# Keyboard monitoring (evdev)
# ---------------------------------------------------------------------------

def _trigger(mode: str) -> None:
    global _last_toggle

    # Don't steal a recording started by the other key
    if _recording and mode != _current_mode:
        return

    now = time.time()
    if now - _last_toggle < TOGGLE_COOLDOWN:
        return
    _last_toggle = now

    if not _recording:
        _start_recording(mode)
    else:
        target = _transcribe_and_paste if mode == "paste" else _transcribe_and_chat
        threading.Thread(target=target, daemon=True).start()


def _monitor_device(device: InputDevice) -> None:
    logger.info("Monitoring: %s (%s)", device.name, device.path)
    try:
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            key_event = categorize(event)
            keycode = key_event.keycode
            if isinstance(keycode, list):
                keycode = keycode[0]

            if keycode == "KEY_RIGHTCTRL":
                if key_event.keystate == 1:    # down
                    _ctrl_released.clear()
                    _trigger("paste")
                elif key_event.keystate == 0:  # up
                    _ctrl_released.set()

            elif keycode == "KEY_RIGHTALT":
                if key_event.keystate == 1:    # down
                    _alt_released.clear()
                    _trigger("voice")
                elif key_event.keystate == 0:  # up
                    _alt_released.set()

    except OSError:
        logger.warning("Device disconnected: %s", device.path)


def _find_keyboards() -> list[InputDevice]:
    found, permission_errors = [], []
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY not in caps:
                continue
            keys = caps[ecodes.EV_KEY]
            if ecodes.KEY_A in keys and ecodes.KEY_RIGHTCTRL in keys:
                found.append(dev)
        except PermissionError:
            permission_errors.append(path)
        except Exception:
            pass

    if permission_errors and not found:
        print("✗ Permission denied reading input devices.")
        print("  Run: sudo usermod -a -G input $USER  then log out/in (or: newgrp input)")
        sys.exit(1)

    return found


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("Lunaschal Voice Input")
    print(f"  STT/TTS service : {STT_URL}")
    print(f"  Lunaschal server: {LUNASCHAL_URL}")
    print("  Right Ctrl      : record → paste transcription at cursor")
    print("  Right Alt       : record → AI chat → speak reply")
    if WAKE_WORD_MODEL:
        print(f"  Wake word       : \"Hey Luna\"  (WAKE_WORD_MODEL={WAKE_WORD_MODEL})")
    else:
        print("  Wake word       : disabled  (set WAKE_WORD_MODEL=/path/to/hey_luna.onnx)")
    print("  Exit            : Ctrl+C\n")

    keyboards = _find_keyboards()
    if not keyboards:
        print("✗ No keyboard devices found. Are you in the 'input' group?")
        print("  Run: sudo usermod -a -G input $USER  then log out/in")
        sys.exit(1)

    for kb in keyboards:
        print(f"  Keyboard: {kb.name}  ({kb.path})")
    print()

    try:
        r = requests.get(f"{STT_URL}/health", timeout=2)
        data = r.json()
        stt_ok = data.get("stt_ready", data.get("ready", False))
        tts_ok = data.get("tts_ready", False)
        print(f"  STT: {'✓ ready' if stt_ok else '⚠ loading'}  [{data.get('stt_model', data.get('model', '?'))}]")
        print(f"  TTS: {'✓ ready' if tts_ok else '⚠ loading or unavailable'}")
    except Exception:
        print(f"  ⚠ STT/TTS service not reachable at {STT_URL}")
        print("    Start it: ./stt/run_service.sh")

    print("\nWaiting for shortcut…\n")

    threading.Thread(target=_wake_word_loop, daemon=True).start()

    for kb in keyboards:
        threading.Thread(target=_monitor_device, args=(kb,), daemon=True).start()

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nExiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
