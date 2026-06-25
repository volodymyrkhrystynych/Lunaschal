#!/usr/bin/env bash
# Sets up two virtual environments:
#
#   stt/.venv  — listener only (evdev, sounddevice, audio capture)
#   ../.venv   — main Flask app; also receives local AI deps in local mode
#                (openai-whisper via requirements.txt, kokoro-onnx added here)
#
# Usage:
#   bash stt/setup.sh           # full local setup (openai-whisper, kokoro-onnx, openwakeword)
#   bash stt/setup.sh --api     # API-only setup (openai client only, no heavy local models)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
ROOT_VENV="$(dirname "$SCRIPT_DIR")/.venv"

MODE="local"
for arg in "$@"; do
    case "$arg" in
        --api) MODE="api" ;;
    esac
done

echo "Setup mode: $MODE"
echo "Creating virtual environment at $VENV ..."
python3 -m venv "$VENV"

echo "Installing base Python dependencies..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

if [ "$MODE" = "local" ]; then
    echo "Installing local AI dependencies into listener venv (standalone service)..."
    "$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements-local.txt"
    # openwakeword has no Python 3.14 wheel for tflite-runtime; install without
    # deps since onnxruntime (from kokoro-onnx) covers the onnx inference backend.
    "$VENV/bin/pip" install --no-deps openwakeword

    # Install kokoro-onnx into the main Flask venv so local TTS works in-process.
    # openai-whisper is already listed in ../requirements.txt.
    if [ -f "$ROOT_VENV/bin/pip" ]; then
        echo "Installing kokoro-onnx into main Flask venv ($ROOT_VENV)..."
        "$ROOT_VENV/bin/pip" install "kokoro-onnx>=0.4.0"
    else
        echo "Warning: main .venv not found at $ROOT_VENV"
        echo "  Run: pip install kokoro-onnx>=0.4.0  (in your Flask venv)"
    fi
fi

echo ""
echo "Done. System packages also required:"
echo "  sudo pacman -S wtype portaudio"
echo ""
if [ "$MODE" = "api" ]; then
    echo "API mode: set these env vars before running:"
    echo "  export OPENAI_API_KEY=sk-..."
    echo "  export STT_BACKEND=openai"
    echo "  export TTS_BACKEND=openai"
    echo ""
fi
echo "Start the Flask app (handles STT/TTS): npm run dev"
echo "Start the voice listener:              ./stt/run_listener.sh"
