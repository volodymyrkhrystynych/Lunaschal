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
LUNASCHAL_URL = os.environ.get("LUNASCHAL_URL", "http://127.0.0.1:3000")

SAMPLE_RATE     = 16000
CHANNELS        = 1
TOGGLE_COOLDOWN = 0.5      # seconds — prevents double-fire on key hold
KEY_RELEASE_TIMEOUT = 3.0  # seconds to wait for trigger key release before typing

# --- Voice assistant conversation history (in-memory) ---
_voice_history: list[dict] = [
    {
        "role": "system",
        "content": (
            "You are a helpful voice assistant. "
            "Keep responses concise and conversational — "
            "your reply will be spoken aloud."
        ),
    }
]

# --- Recording state ---
_recording      = False
_current_mode   = "paste"   # "paste" | "voice"
_stream: Optional[sd.InputStream] = None
_audio_chunks: list[np.ndarray]   = []
_last_toggle    = 0.0

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

    # Wait for Right Alt to be released before speaking
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

    _speak(_strip_emojis(reply))


def _chat(user_text: str) -> str | None:
    """Send conversation history to Lunaschal's streaming chat endpoint, return full reply."""
    # Send only role/content (strip system message which Lunaschal's endpoint doesn't expect)
    messages = [m for m in _voice_history if m["role"] != "system"]
    try:
        with requests.post(
            f"{LUNASCHAL_URL}/api/chat/stream",
            json={"messages": messages},
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


def _strip_emojis(text: str) -> str:
    # Remove emoji and other pictographic/symbol characters
    return re.sub(r'[\U00010000-\U0010FFFF\U00002600-\U000027BF\U0001F000-\U0001FFFF]', '', text).strip()


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

    for kb in keyboards:
        threading.Thread(target=_monitor_device, args=(kb,), daemon=True).start()

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nExiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
