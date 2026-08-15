#!/usr/bin/env bash
# Bring up the self-hosted SearXNG stack.
#
# Prefer the systemd unit next to this file (lunaschal-searxng.service) so it
# auto-starts on login like llama-server does. This script is the manual
# equivalent for when the unit isn't installed.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v docker > /dev/null 2>&1; then
  echo "docker not found — install Docker first." >&2
  exit 1
fi

docker compose up -d
echo "SearXNG starting — http://localhost:8888"
