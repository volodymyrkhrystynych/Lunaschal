#!/usr/bin/env bash
# Rebuild and restart the local production instance (lunaschal.service) after
# hand-editing on the machine the service runs on — the same three steps as
# deploy-check.sh's tail end, but for local changes it never pulled.
#
# Not run by any timer; invoke by hand after saving changes you want :5000 to
# pick up.
set -e

cd "$(dirname "$0")/.."

echo "Building..."
npm run build

echo "Reloading systemd unit files..."
systemctl --user daemon-reload

echo "Restarting lunaschal.service..."
systemctl --user restart lunaschal.service

echo "$(date -Iseconds) restart-prod: restarted at $(git rev-parse --short HEAD)"
