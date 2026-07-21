#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Load .env from repo root if present
if [ -f .env ]; then
  set -a; source .env; set +a
fi

# LUNASCHAL_URL must point to the server. The server is HTTPS-only
# (start-server.sh wires a Tailscale cert into Flask so iOS Safari can access
# the mic), and the cert is issued for the server's MagicDNS hostname — so use
# that hostname, not the Tailscale IP, or the listener's cert check fails.
# Example: export LUNASCHAL_URL=https://<name>.<tailnet>.ts.net:5000
# Or add it to .env in this repo root
if [ -z "$LUNASCHAL_URL" ]; then
  echo "Error: LUNASCHAL_URL is not set."
  echo "  Export it:  export LUNASCHAL_URL=https://<name>.<tailnet>.ts.net:5000"
  echo "  Or add it to .env in the repo root"
  exit 1
fi

case "$LUNASCHAL_URL" in
  https://*) ;;
  *)
    echo "Error: LUNASCHAL_URL must be an https:// URL — the server is HTTPS-only"
    echo "  (start-server.sh serves Flask with a Tailscale cert for mic access on iOS)."
    echo "  The cert only validates for the server's MagicDNS hostname, so point at"
    echo "  that hostname, not the IP:  export LUNASCHAL_URL=https://<name>.<tailnet>.ts.net:5000"
    exit 1
    ;;
esac

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
