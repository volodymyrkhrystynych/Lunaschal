#!/usr/bin/env python3
"""
Lunaschal Voice Input Listener

Shortcut: Right Ctrl — toggles recording on/off.
After stopping, audio is transcribed and typed at the current cursor position.

Uses evdev to read input events directly from /dev/input/event*, so the
shortcut works globally on Wayland and X11 regardless of which window has focus.

Requirements:
  - User must be in the 'input' group:
      sudo usermod -a -G input $USER   (then log out/in or: newgrp input)
  - STT service running: ./stt/run_service.sh
  - wtype installed: sudo pacman -S wtype

Environment variables:
  STT_URL   URL of the STT service (default: http://127.0.0.1:8765)
"""

import io
import os
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

STT_URL = os.environ.get("STT_URL", "http://127.0.0.1:8765")
SAMPLE_RATE = 16000
CHANNELS = 1
TOGGLE_COOLDOWN = 0.5       # seconds — prevents double-fire on key hold
KEY_RELEASE_TIMEOUT = 3.0   # seconds to wait for Right Ctrl to be released before typing

KEY_TRIGGER = ecodes.KEY_RIGHTCTRL

# --- State ---
_recording = False
_stream: Optional[sd.InputStream] = None
_audio_chunks: list[np.ndarray] = []
_last_toggle = 0.0
_ctrl_released = threading.Event()
_ctrl_released.set()  # starts in "released" state


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


def _start_recording() -> None:
    global _recording, _stream, _audio_chunks
    _audio_chunks = []
    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        callback=_audio_callback,
    )
    _stream.start()
    _recording = True
    _status("🎙️  Recording… (Right Ctrl to stop)")
    logger.info("Recording started")


# ---------------------------------------------------------------------------
# Transcription + paste
# ---------------------------------------------------------------------------

def _transcribe_and_paste() -> None:
    global _recording, _stream

    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None
    _recording = False

    if not _audio_chunks:
        _status("✗ No audio captured")
        return

    audio = np.concatenate(_audio_chunks, axis=0)
    duration = len(audio) / SAMPLE_RATE
    _status(f"⏳ Transcribing {duration:.1f}s…")
    logger.info("Stopped recording — %.1fs captured", duration)

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
        return
    except Exception as e:
        _status(f"✗ Error: {e}")
        logger.error("Request error: %s", e)
        return

    if not text:
        _status("✗ No speech detected")
        return

    preview = text[:65] + ("…" if len(text) > 65 else "")
    _status(f"✓ {preview}")
    logger.info('Transcribed: "%s"', text)

    # Wait for Right Ctrl to be physically released before typing so the
    # compositor doesn't apply Ctrl as a modifier to every character wtype sends.
    _ctrl_released.wait(timeout=KEY_RELEASE_TIMEOUT)
    _type_text(text)


def _type_text(text: str) -> None:
    """Paste text at the current cursor via clipboard + Ctrl+V.

    Using wtype to type characters directly fails in Electron apps (Discord,
    VS Code, etc.) because Chromium misinterprets virtual key events as raw
    scan codes. The clipboard approach works universally.
    """
    # Write to clipboard
    proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))

    # Small delay to ensure the clipboard is populated before the paste
    time.sleep(0.05)

    # Simulate Ctrl+V
    try:
        subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=True)
    except FileNotFoundError:
        _status("✓ Copied to clipboard — paste with Ctrl+V  (install wtype to auto-paste)")
    except subprocess.CalledProcessError as e:
        logger.error("wtype error: %s", e)


# ---------------------------------------------------------------------------
# Keyboard monitoring (evdev)
# ---------------------------------------------------------------------------

def _toggle() -> None:
    global _last_toggle
    now = time.time()
    if now - _last_toggle < TOGGLE_COOLDOWN:
        return
    _last_toggle = now

    if not _recording:
        _start_recording()
    else:
        threading.Thread(target=_transcribe_and_paste, daemon=True).start()


def _monitor_device(device: InputDevice) -> None:
    """Read events from a single keyboard device in a loop."""
    logger.info("Monitoring: %s (%s)", device.name, device.path)
    try:
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            key_event = categorize(event)
            keycode = key_event.keycode
            # keycode can be a list when multiple keys share a scancode
            if isinstance(keycode, list):
                keycode = keycode[0]

            if keycode != "KEY_RIGHTCTRL":
                continue

            if key_event.keystate == 1:   # key down
                _ctrl_released.clear()
                _toggle()
            elif key_event.keystate == 0: # key up
                _ctrl_released.set()
    except OSError:
        logger.warning("Device disconnected: %s", device.path)


def _find_keyboards() -> list[InputDevice]:
    """Return all evdev devices that look like keyboards with Right Ctrl."""
    found = []
    permission_errors = []

    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY not in caps:
                continue
            keys = caps[ecodes.EV_KEY]
            # Must have alphabetic keys AND the trigger key to count as a keyboard
            if ecodes.KEY_A in keys and KEY_TRIGGER in keys:
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
    print(f"  STT service : {STT_URL}")
    print("  Shortcut    : Right Ctrl  (toggle recording)")
    print("  Backend     : evdev (global — works on all windows/screens)")
    print("  Exit        : Ctrl+C\n")

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
        if data.get("ready"):
            print(f"✓ STT service ready  [{data.get('model')}]\n")
        else:
            print("⚠ STT service is still loading the model, will retry on first use.\n")
    except Exception:
        print(f"⚠ STT service not reachable at {STT_URL}")
        print("  Start it in another terminal: ./stt/run_service.sh\n")

    print("Waiting for shortcut…\n")

    # Each keyboard device gets its own monitoring thread
    threads = []
    for kb in keyboards:
        t = threading.Thread(target=_monitor_device, args=(kb,), daemon=True)
        t.start()
        threads.append(t)

    try:
        # Keep main thread alive
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nExiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
