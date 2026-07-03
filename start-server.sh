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

# Kill any leftover processes from a previous session
for port in 5000 5173; do
  pids=$(lsof -ti tcp:$port 2>/dev/null) && kill $pids 2>/dev/null && echo "Killed stale process on :$port" || true
done

# Start ollama if not already running
if ! pgrep -x ollama > /dev/null; then
  echo "Starting ollama..."
  ollama serve &>/tmp/ollama.log &
fi

# Start Flask + Vite dev servers
npm run dev &
DEV_PID=$!

# Wait for Flask to be ready
echo "Waiting for Flask..."
until curl -sf http://127.0.0.1:5000/api/health > /dev/null; do sleep 0.5; done

echo "Server ready. Nodes can connect at http://$(tailscale ip -4 2>/dev/null || hostname -I | awk '{print $1}'):5000"

# Open the desktop window
# (voice listener is managed by Flask via STT_LISTENER=1 in .env)
.venv/bin/python main.py --dev

# Kill everything when the window closes
kill $DEV_PID 2>/dev/null
