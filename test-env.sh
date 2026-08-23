#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Everything below is scratch, under data/ (already gitignored wholesale) so
# nothing new needs adding to .gitignore, and it never overlaps the real
# ./data/lunaschal.db or the production media roots those same env vars
# default to (backend/db/connection.py, backend/storage.py).
SCRATCH="$(pwd)/data/test-run"

export DATABASE_URL="$SCRATCH/lunaschal-test.db"
export FANFIC_ROOT="$SCRATCH/fanfic"
export MEETINGS_ROOT="$SCRATCH/meetings"
export JOURNAL_ROOT="$SCRATCH/journal"
export JOURNAL_DRAFTS_ROOT="$SCRATCH/journal_drafts"
export LIFESTYLE_ROOT="$SCRATCH/lifestyle"
export FOOD_ROOT="$SCRATCH/food"
export RECIPE_ROOT="$SCRATCH/recipes"
export CHAT_ROOT="$SCRATCH/chat"
export PAPER_ROOT="$SCRATCH/paper"
export JOBS_ROOT="$SCRATCH/jobs"
export NEWSPAPERS_ROOT="$SCRATCH/newspapers"
export NOTEBOOK_ROOT="$SCRATCH/notebook"
export EMAIL_MEDIA_ROOT="$SCRATCH/email/media"
export SHORTCUTS_PATH="$SCRATCH/shortcuts.json"

# Same idiom backend/tests/conftest.py uses: keep every daemon scheduler off
# so nothing mutates the seeded data or makes a stray call in the background.
export LUNASCHAL_NO_SCHEDULERS=1
# STT_LISTENER deliberately left unset — it's opt-in already (backend/app.py).

echo "Rebuilding seed database at $DATABASE_URL ..."
.venv/bin/python scripts/seed_test_db.py

# Detect: is a real llama-server (production, big models) already up on
# :8080? If so, leave it completely alone and just point at it like normal.
# If not, this is the weak-laptop case: start a tiny one for this session only.
STARTED_TINY_LLAMA=0
LLAMA_PID=""
if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
  echo "llama-server already running on :8080 — leaving it alone (assuming production models)."
else
  echo "No llama-server on :8080 — starting a tiny one for this session."
  LLAMA_PRESETS="$(pwd)/llama/presets.tiny.ini" ./llama/start-llama.sh &
  LLAMA_PID=$!
  STARTED_TINY_LLAMA=1
  echo "Waiting for the tiny llama-server..."
  until curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; do sleep 0.5; done
fi

DEV_PID=""

cleanup() {
  if [ -n "$DEV_PID" ]; then
    kill "$DEV_PID" 2>/dev/null || true
  fi
  # Only ever kill the tiny llama-server THIS script started — never a
  # pre-existing/production one it merely found and reused.
  if [ "$STARTED_TINY_LLAMA" = "1" ] && [ -n "$LLAMA_PID" ]; then
    echo "Stopping the tiny llama-server this session started..."
    kill "$LLAMA_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

# Stale dev-port cleanup, same idiom as start.sh — :5000 (production) is
# deliberately never touched.
for port in 5001 5173; do
  pids=$(lsof -ti tcp:$port 2>/dev/null) && kill $pids 2>/dev/null && echo "Killed stale process on :$port" || true
done

npm run dev &
DEV_PID=$!

echo "Waiting for Flask..."
until curl -sf http://127.0.0.1:5001/api/health > /dev/null; do sleep 0.5; done

if [ "$STARTED_TINY_LLAMA" = "1" ]; then
  TIER="tiny local"
else
  TIER="production"
fi
echo "Test environment ready: http://localhost:5173 (dummy data, $TIER models). Ctrl+C to stop."

wait "$DEV_PID"
