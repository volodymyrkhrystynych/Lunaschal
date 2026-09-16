#!/usr/bin/env bash
# Restart llama-server and wait until it actually answers again.
#
# Prefers the systemd unit next to this file — that is how the server is meant to
# run, so restarting the unit is the only way the restart survives this terminal.
# Falls back to killing whatever is on the port and re-running start-llama.sh for
# a box where the unit was never installed.
#
# The wait is the point: the model takes tens of seconds to load, so a restart
# that returns immediately just moves the failure to the next request.
set -euo pipefail

UNIT="${LLAMA_UNIT:-lunaschal-llama}"
PORT="${LLAMA_PORT:-8080}"
TIMEOUT="${LLAMA_WAIT:-180}"
HERE="$(cd "$(dirname "$0")" && pwd)"

have_unit() {
  systemctl --user cat "$UNIT" >/dev/null 2>&1
}

if have_unit; then
  echo "Restarting $UNIT…"
  systemctl --user restart "$UNIT"
else
  echo "$UNIT is not installed — restarting llama-server by hand." >&2
  echo "(see $HERE/lunaschal-llama.service to install it)" >&2
  pkill -f 'llama-server .*--models-preset' || true
  # Give the old process a moment to release the port and its VRAM before the
  # new one tries to claim both.
  for _ in $(seq 20); do
    ss -ltn "sport = :$PORT" 2>/dev/null | grep -q ":$PORT" || break
    sleep 0.5
  done
  LLAMA_PORT="$PORT" nohup "$HERE/start-llama.sh" >/dev/null 2>&1 &
fi

printf 'Waiting for :%s to come back' "$PORT"
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo
    echo "llama-server is up on :$PORT"
    exit 0
  fi
  printf '.'
  sleep 2
done

echo
echo "llama-server did not answer on :$PORT within ${TIMEOUT}s" >&2
if have_unit; then
  echo "Check: journalctl --user -u $UNIT -n 50" >&2
fi
exit 1
