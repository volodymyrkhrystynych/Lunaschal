#!/usr/bin/env bash
# Production launcher for the systemd-managed lunaschal.service.
#
# Runs the built `dist/` through main.py --headless: Flask in the foreground,
# serving on :5000, with no PyWebView window.
#
# It is headless because the windowed path exits 0 when its window is closed.
# Under Restart=on-failure that read as a clean shutdown, so closing the window
# took the whole LAN server down — phone, Pocket 2 and tablet with it — and
# systemd rightly refused to restart something that had succeeded. A server's
# lifetime must not be tied to a window somebody can click away. It also keeps
# ~1.4G of QtWebEngine out of an always-on service (108MB RSS vs a 1.5G peak).
#
# To open the UI, use ops/open-window.sh or any browser — both just point at
# the same https://<tailscale-host>:5000.
#
# Run `npm run build` before (re)starting this — the deploy watcher
# (ops/deploy-check.sh) does that on every auto-pull; a manual restart after
# local changes needs it run by hand.
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

exec .venv/bin/python main.py --headless
