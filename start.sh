#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

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
