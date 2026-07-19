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

# Flask serves HTTPS-only in network mode (see start-server.sh) — without
# this, LUNASCHAL_URL's default http://127.0.0.1:5000 gets a connection
# reset and every voice shortcut silently stops working.
if [ -z "$LUNASCHAL_URL" ] && [ "$NETWORK_MODE" = "1" ]; then
    CERT_FILE=$(ls "$ROOT_DIR"/certs/*.crt 2>/dev/null | head -1)
    if [ -n "$CERT_FILE" ]; then
        export LUNASCHAL_URL="https://$(basename "$CERT_FILE" .crt):5000"
    fi
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/listener.py"
