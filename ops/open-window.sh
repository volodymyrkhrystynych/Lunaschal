#!/usr/bin/env bash
# Open the Lunaschal desktop window against the already-running production
# server (lunaschal.service).
#
# Production is headless — see ops/run-prod.sh — so the window is a *client*
# here, not the server. Closing it stops nothing; the phone and the Pocket 2
# keep talking to :5000 either way. That separation is the whole point: this
# script can be run, closed and re-run as often as you like.
#
# main.py --server-url takes the 'none' wait path: it opens the URL directly
# without starting or waiting for any local Flask.
set -e

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; source .env; set +a
fi

PORT="${LUNASCHAL_PORT:-5000}"

# Match run-prod.sh's cert lookup so the window uses the same hostname the cert
# is issued for — https://localhost:5000 would fail verification.
CERT_FILE=$(ls certs/*.crt 2>/dev/null | head -1)
if [ -n "$CERT_FILE" ]; then
  HOST=$(basename "$CERT_FILE" .crt)
  URL="https://$HOST:$PORT"
else
  URL="http://127.0.0.1:$PORT"
fi

if ! curl -skf --max-time 5 "$URL/api/health" > /dev/null; then
  echo "Error: no Lunaschal server responding at $URL"
  echo "  Start it with:  systemctl --user start lunaschal"
  exit 1
fi

echo "Opening window against $URL"
exec .venv/bin/python main.py --server-url "$URL"
