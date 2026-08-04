#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Load .env from repo root if present
if [ -f .env ]; then
  set -a; source .env; set +a
fi

export NETWORK_MODE=1

if [ -z "$LUNASCHAL_PASSWORD" ]; then
  echo "Error: LUNASCHAL_PASSWORD is not set. Export it or add it to .env"
  exit 1
fi

# iOS Safari only exposes navigator.mediaDevices (mic access) on a secure
# context, so LAN/Tailscale clients need real HTTPS, not just the password.
# Get a cert via: sudo tailscale cert --cert-file=./certs/<name>.crt --key-file=./certs/<name>.key <magicdns-name>
CERT_FILE=$(ls certs/*.crt 2>/dev/null | head -1)
KEY_FILE=$(ls certs/*.key 2>/dev/null | head -1)
if [ -z "$CERT_FILE" ] || [ -z "$KEY_FILE" ]; then
  echo "Error: no TLS cert found in certs/. See start-server.sh for how to generate one with 'tailscale cert'."
  exit 1
fi
export TAILSCALE_HOSTNAME=$(basename "$CERT_FILE" .crt)
export VITE_HTTPS_CERT="$CERT_FILE"
export VITE_HTTPS_KEY="$KEY_FILE"

# Kill any leftover processes from a previous session. :5000 is excluded on
# purpose — that is the always-on production server (lunaschal.service), not
# ours to kill. Dev owns :5001 and :5173.
for port in 5001 5173; do
  pids=$(lsof -ti tcp:$port 2>/dev/null) && kill $pids 2>/dev/null && echo "Killed stale process on :$port" || true
done

# llama-server is assumed to be running already — see llama/lunaschal-llama.service.
if ! curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
  echo "WARNING: llama-server is not responding on :8080 — AI features will fail."
  echo "  Start it with:  systemctl --user start lunaschal-llama"
  echo "  Or, without the unit installed:  ./llama/start-llama.sh"
fi

# Start Flask (bound to all interfaces, TLS) + Vite dev servers
./node_modules/.bin/concurrently \
  ".venv/bin/flask --app backend.app run --host 0.0.0.0 --port 5001 --debug --cert=$CERT_FILE --key=$KEY_FILE" \
  "./node_modules/.bin/vite --host" &
DEV_PID=$!

# Wait for Flask to be ready (-k: verifying 127.0.0.1 against a cert issued
# for the Tailscale hostname would otherwise fail this local readiness check)
echo "Waiting for Flask..."
until curl -skf https://127.0.0.1:5001/api/health > /dev/null; do sleep 0.5; done

echo "Server ready. Nodes can connect at https://$TAILSCALE_HOSTNAME:5173"

# Open the desktop window
# (voice listener is managed by Flask via STT_LISTENER=1 in .env)
.venv/bin/python main.py --dev

# Kill everything when the window closes
kill $DEV_PID 2>/dev/null
