#!/usr/bin/env bash
# Starts the faster-whisper transcription service.
# First run downloads the model (~1.5 GB).
#
# Options via environment:
#   WHISPER_MODEL=large-v3-turbo  (default)
#   WHISPER_DEVICE=cuda           (default)
#   STT_PORT=8765                 (default)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV/bin/python" ]; then
    echo "Virtual environment not found. Run: bash stt/setup.sh"
    exit 1
fi

# CUDA 13 is installed but ctranslate2 was built against CUDA 12.
# Provide symlinked .so.12 stubs so the loader finds them.
export LD_LIBRARY_PATH="$SCRIPT_DIR/lib:${LD_LIBRARY_PATH:-}"

exec "$VENV/bin/python" "$SCRIPT_DIR/service.py"
