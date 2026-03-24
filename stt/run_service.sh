#!/usr/bin/env bash
# Starts the Lunaschal STT+TTS service on port 8765.
#
# --- Local AI mode (default) ---
#   WHISPER_MODEL=large-v3-turbo   (first run downloads ~1.5 GB)
#   WHISPER_DEVICE=cuda            (or cpu)
#   STT_PORT=8765
#
# --- OpenAI API mode ---
#   export OPENAI_API_KEY=sk-...
#   export STT_BACKEND=openai
#   export TTS_BACKEND=openai
#   export OPENAI_TTS_VOICE=nova   (alloy/echo/fable/onyx/nova/shimmer)
#
# Mix and match backends independently, e.g. STT_BACKEND=openai TTS_BACKEND=local

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$SCRIPT_DIR/.venv"

# Load .env from repo root if present
if [ -f "$ROOT_DIR/.env" ]; then
    set -a; source "$ROOT_DIR/.env"; set +a
fi

if [ ! -f "$VENV/bin/python" ]; then
    echo "Virtual environment not found. Run: bash stt/setup.sh"
    exit 1
fi

# CUDA 13 is installed but ctranslate2 was built against CUDA 12.
# Provide symlinked .so.12 stubs so the loader finds them.
export LD_LIBRARY_PATH="$SCRIPT_DIR/lib:${LD_LIBRARY_PATH:-}"

exec "$VENV/bin/python" "$SCRIPT_DIR/service.py"
