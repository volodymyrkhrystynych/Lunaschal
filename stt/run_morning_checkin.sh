#!/usr/bin/env bash
# Morning check-in daemon — monitors for wake-from-sleep and starts a voice
# planning conversation when the computer wakes between 8 AM and 11 AM.
#
# Usage:
#   ./stt/run_morning_checkin.sh         # run as daemon (background monitor)
#   ./stt/run_morning_checkin.sh --now   # run check-in immediately (for testing)
#
# Options via environment:
#   STT_URL=http://127.0.0.1:8765       (default)
#   LUNASCHAL_URL=http://127.0.0.1:7842 (default)
#   MORNING_START_HOUR=8                (default)
#   MORNING_END_HOUR=11                 (default)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV/bin/python" ]; then
    echo "Virtual environment not found. Run: bash stt/setup.sh"
    exit 1
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/morning_checkin.py" "$@"
