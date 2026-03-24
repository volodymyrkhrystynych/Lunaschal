#!/usr/bin/env bash
# Creates the Python venv and installs STT dependencies.
#
# Usage:
#   bash stt/setup.sh           # full local setup (faster-whisper, kokoro-onnx, openwakeword)
#   bash stt/setup.sh --api     # API-only setup (openai client only, no heavy local models)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

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
    echo "Installing local AI dependencies (faster-whisper, kokoro-onnx, openwakeword)..."
    "$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements-local.txt"

    # Create CUDA compatibility symlinks.
    # ctranslate2 wheels are built against CUDA 12 (libcublas.so.12) but Arch
    # ships CUDA 13 (libcublas.so.13). Symlink inside stt/lib/ so we don't
    # touch system files. run_service.sh prepends this dir to LD_LIBRARY_PATH.
    CUDA_LIB="${CUDA_LIB:-/opt/cuda/lib64}"
    if [ -f "$CUDA_LIB/libcublas.so.13" ]; then
        echo "Creating CUDA 12→13 compatibility symlinks in stt/lib/ ..."
        mkdir -p "$SCRIPT_DIR/lib"
        ln -sf "$CUDA_LIB/libcublas.so.13"   "$SCRIPT_DIR/lib/libcublas.so.12"
        ln -sf "$CUDA_LIB/libcublasLt.so.13" "$SCRIPT_DIR/lib/libcublasLt.so.12"
    fi
fi

echo ""
echo "Done. System packages also required:"
echo "  sudo pacman -S wtype portaudio"
echo ""
if [ "$MODE" = "api" ]; then
    echo "API mode: set these env vars before running the service:"
    echo "  export OPENAI_API_KEY=sk-..."
    echo "  export STT_BACKEND=openai"
    echo "  export TTS_BACKEND=openai"
    echo ""
fi
echo "Start the STT service:    ./stt/run_service.sh"
echo "Start the voice listener: ./stt/run_listener.sh"
