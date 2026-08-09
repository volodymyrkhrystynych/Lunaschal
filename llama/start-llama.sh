#!/usr/bin/env bash
# Start llama-server in router mode with Lunaschal's presets.
#
# Runs independently of Flask on purpose: `npm run dev` reloads Flask on every
# save, and this model takes tens of seconds to load. Keeping it in its own
# process (ideally via the systemd unit next to this file) means a backend restart
# no longer costs a model reload.
set -euo pipefail

LLAMA_BIN="${LLAMA_BIN:-$HOME/.local/opt/llama.cpp/build/bin/llama-server}"
PRESETS="${LLAMA_PRESETS:-$(cd "$(dirname "$0")" && pwd)/presets.ini}"
PORT="${LLAMA_PORT:-8080}"

if [ ! -x "$LLAMA_BIN" ]; then
  echo "llama-server not found at $LLAMA_BIN" >&2
  echo "Build it first — see docs/learnings/moe-expert-placement.md" >&2
  exit 1
fi

# --models-max 3: exactly the three presets in presets.ini — qwen36,
# gemma4-12b-omni and embed — resident together. Three rather than two because
# evicting any of them is expensive on the interactive path: re-loading 22 GB of
# Qwen costs tens of seconds, and the embedder is called from the Learning flow
# while a card is on screen. If the omni model ever causes memory pressure
# (it is another ~7.4 GB of page cache), drop this to 2 and let it reload on
# demand — its two callers are opt-in background jobs with no one waiting.
exec "$LLAMA_BIN" \
  --models-preset "$PRESETS" \
  --models-max 3 \
  --host 127.0.0.1 \
  --port "$PORT" \
  "$@"
