#!/usr/bin/env bash
# Starts the voice input listener in the foreground.
# Runs until Ctrl+C. Launch in a terminal or as a background process.

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

exec "$VENV/bin/python" "$SCRIPT_DIR/listener.py"
