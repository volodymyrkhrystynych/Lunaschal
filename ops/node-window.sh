#!/usr/bin/env bash
# Open the Lunaschal desktop window on a weak node machine (e.g. the Pocket 2)
# against an already-running *production* server elsewhere on the tailnet.
#
# Like open-window.sh, this is a client only — closing the window stops
# nothing on the server. Unlike open-window.sh, it doesn't read a local
# certs/*.crt file (node machines don't have one); the server URL comes from
# LUNASCHAL_URL instead, same as start-node.sh. Because the target server
# already serves its own built dist/, there's no local Vite/Flask to run
# here — main.py --server-url takes the 'none' wait path and opens the URL
# directly.
set -e

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; source .env; set +a
fi

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
    exit 1
    ;;
esac

if ! curl -skf --max-time 5 "$LUNASCHAL_URL/api/health" > /dev/null; then
  echo "Error: no Lunaschal server responding at $LUNASCHAL_URL"
  exit 1
fi

# Both STT and chat calls go to the same server
export STT_URL="$LUNASCHAL_URL"

echo "Connecting to server at $LUNASCHAL_URL"

# Start STT listener in background
./stt/run_listener.sh &>/tmp/lunaschal-listener.log &
LISTENER_PID=$!

echo "Opening window against $LUNASCHAL_URL"
.venv/bin/python main.py --server-url "$LUNASCHAL_URL"

kill $LISTENER_PID 2>/dev/null
