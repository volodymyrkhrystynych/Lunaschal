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

# Serve the frontend locally (fast: no waiting on the server's dist/ to be
# rebuilt, just `git pull` here) while proxying /api calls to the remote server.
VITE_API_PROXY_TARGET="$LUNASCHAL_URL" npm run dev:client &>/tmp/lunaschal-vite.log &
VITE_PID=$!

# Open native desktop window against the local Vite dev server; --server-url
# tells main.py the backend lives remotely, so it waits for Vite instead of
# spawning a local Flask process.
.venv/bin/python main.py --dev --server-url "$LUNASCHAL_URL"

kill $LISTENER_PID $VITE_PID 2>/dev/null
