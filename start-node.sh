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

# Open the app in browser
if command -v xdg-open &>/dev/null; then
  xdg-open "$LUNASCHAL_URL" &
elif command -v open &>/dev/null; then
  open "$LUNASCHAL_URL" &
else
  echo "Open $LUNASCHAL_URL in your browser"
fi

# Start STT listener pointing to remote server
exec ./stt/run_listener.sh
