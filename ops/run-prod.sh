#!/usr/bin/env bash
# Production launcher for the systemd-managed lunaschal.service.
#
# Unlike start-server.sh (dev servers, --dev flag), this runs the built `dist/`
# through main.py's no-flags "flask-spawn" path: main.py starts Flask itself
# and opens the PyWebView window against it directly. Run `npm run build` before
# (re)starting this — the deploy watcher (ops/deploy-check.sh) does that on
# every auto-pull; a manual restart after local changes needs it run by hand.
set -e

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; source .env; set +a
fi

export NETWORK_MODE=1

if [ -z "$LUNASCHAL_PASSWORD" ]; then
  echo "Error: LUNASCHAL_PASSWORD is not set. Export it or add it to .env"
  exit 1
fi

# Same Tailscale-cert convention as start-server.sh, so LAN/Tailscale clients
# (Pocket 2, phone, the backup tablet's browser) get real HTTPS.
CERT_FILE=$(ls certs/*.crt 2>/dev/null | head -1)
KEY_FILE=$(ls certs/*.key 2>/dev/null | head -1)
if [ -z "$CERT_FILE" ] || [ -z "$KEY_FILE" ]; then
  echo "Error: no TLS cert found in certs/. See start-server.sh for how to generate one with 'tailscale cert'."
  exit 1
fi
export TAILSCALE_HOSTNAME=$(basename "$CERT_FILE" .crt)
export VITE_HTTPS_CERT="$CERT_FILE"
export VITE_HTTPS_KEY="$KEY_FILE"

if [ ! -d dist ]; then
  echo "Error: dist/ not found. Run 'npm run build' before starting lunaschal.service."
  exit 1
fi

exec .venv/bin/python main.py
