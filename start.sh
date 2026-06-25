#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

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

# Start voice input listener in background
./stt/run_listener.sh &>/tmp/lunaschal-listener.log &
LISTENER_PID=$!

# Open the desktop window
.venv/bin/python main.py --dev

# Kill everything when the window closes
kill $DEV_PID $LISTENER_PID 2>/dev/null
