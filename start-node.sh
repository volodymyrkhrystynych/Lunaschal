#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Load .env from repo root if present
if [ -f .env ]; then
  set -a; source .env; set +a
fi

# LUNASCHAL_URL must point to the server (Tailscale IP or hostname)
# Example: export LUNASCHAL_URL=http://100.64.x.x:5000
# Or add it to .env in this repo root
if [ -z "$LUNASCHAL_URL" ]; then
  echo "Error: LUNASCHAL_URL is not set."
  echo "  Export it:  export LUNASCHAL_URL=http://100.64.x.x:5000"
  echo "  Or add it to .env in the repo root"
  exit 1
fi

# Both STT and chat calls go to the same server
export STT_URL="$LUNASCHAL_URL"

echo "Connecting to server at $LUNASCHAL_URL"

# Start STT listener in background
./stt/run_listener.sh &>/tmp/lunaschal-listener.log &
LISTENER_PID=$!

# Open native desktop window pointing at the remote server
.venv/bin/python main.py --server-url "$LUNASCHAL_URL"

kill $LISTENER_PID 2>/dev/null
