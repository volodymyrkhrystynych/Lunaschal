#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Kill any leftover processes from a previous session.
#
# :5000 is deliberately NOT in this list. That is the production server
# (lunaschal.service), which now runs full-time — killing it here would take the
# phone and the Pocket 2 offline every time a dev session started. Dev owns
# :5001 and :5173.
for port in 5001 5173; do
  pids=$(lsof -ti tcp:$port 2>/dev/null) && kill $pids 2>/dev/null && echo "Killed stale process on :$port" || true
done

# llama-server is assumed to be already running — install it as a systemd --user
# unit (llama/lunaschal-llama.service) so the model outlives any one session.
# Loading Qwen3.6 35B takes tens of seconds, and this script used to pay that
# cost on every start; it is not something a dev run should own.
if ! curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
  echo "WARNING: llama-server is not responding on :8080 — AI features will fail."
  echo "  Start it with:  systemctl --user start lunaschal-llama"
  echo "  Or, without the unit installed:  ./llama/start-llama.sh"
fi

# SearXNG (self-hosted, Docker) is optional — the
# Ideas research agent and chat delegate degrade to "search unavailable"
# without it, so this is a warning, not a blocker.
if ! curl -sf http://127.0.0.1:8888 > /dev/null 2>&1; then
  echo "NOTE: SearXNG is not responding on :8888 — web search will be unavailable."
  echo "  Start it with:  systemctl --user start lunaschal-searxng"
  echo "  Or, without the unit installed:  ./searxng/start-searxng.sh"
fi

# Start Flask + Vite dev servers
npm run dev &
DEV_PID=$!

# Wait for the *dev* Flask on :5001 — probing :5000 would find the production
# server and report ready before the dev backend had even bound its port.
echo "Waiting for Flask..."
until curl -sf http://127.0.0.1:5001/api/health > /dev/null; do sleep 0.5; done

# Open the desktop window
# (voice listener is managed by Flask via STT_LISTENER=1 in .env)
.venv/bin/python main.py --dev

# Kill everything when the window closes
kill $DEV_PID 2>/dev/null
