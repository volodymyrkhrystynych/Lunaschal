#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Kill any leftover processes from a previous session
for port in 5000 5173; do
  pids=$(lsof -ti tcp:$port 2>/dev/null) && kill $pids 2>/dev/null && echo "Killed stale process on :$port" || true
done

# Start llama-server if nothing is serving on :8080 yet. Prefer running it as a
# systemd --user unit (llama/lunaschal-llama.service) so it outlives this script;
# this is just the fallback for a bare `./start.sh`.
if ! curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
  echo "Starting llama-server..."
  ./llama/start-llama.sh &>/tmp/llama-server.log &
  # Loading Gemma 4 26B takes tens of seconds and Flask's first AI call would
  # otherwise fail; wait, but don't hang forever if the model is missing.
  echo "Waiting for llama-server (this loads ~17GB, give it a minute)..."
  for _ in $(seq 1 180); do
    curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1 && break
    sleep 1
  done
  curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1 \
    || echo "WARNING: llama-server not up — see /tmp/llama-server.log. AI features will fail."
fi

# Start Flask + Vite dev servers
npm run dev &
DEV_PID=$!

# Wait for Flask to be ready
echo "Waiting for Flask..."
until curl -sf http://127.0.0.1:5000/api/health > /dev/null; do sleep 0.5; done

# Open the desktop window
# (voice listener is managed by Flask via STT_LISTENER=1 in .env)
.venv/bin/python main.py --dev

# Kill everything when the window closes
kill $DEV_PID 2>/dev/null
