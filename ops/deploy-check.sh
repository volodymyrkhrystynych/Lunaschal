#!/usr/bin/env bash
# Auto-deploy watcher, run periodically by lunaschal-deploy.timer. Only ever
# acts on a clean `main` checkout — the desktop this runs on doubles as a dev
# box, so a dirty tree or a feature branch checked out means "leave it alone,
# try again next tick" rather than force-switching branches or stashing.
#
# Decision logic (branch/dirty/up-to-date/failed) lives in backend/ops/deploy.py
# so it's unit tested; this script only does the git/npm/systemctl orchestration.
#
# A deploy is not done when `systemctl restart` returns — that happens as soon
# as the process is exec'd, so a commit that crashed on startup used to log
# "deployed" and leave production in a restart loop. Now, in order:
#   1. boot the commit from a throwaway worktree against a copy of the live DB
#      (backend/ops/smoke.py) — a commit that can't start is never pulled;
#   2. fast-forward, install changed deps, build into dist.next and swap it in,
#      so the running server never serves a half-emptied dist/;
#   3. restart and poll /api/health; if it never answers, roll the checkout
#      back to the last sha seen healthy, rebuild, and restart that.
# A commit that fails 1 or 3 is recorded and skipped until origin moves on.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO=$(pwd)

# This watcher executes whatever it pulls (npm ci / pip install / npm run build
# all run unattended), so it only ever trusts the one remote this repo was set
# up with — not whatever `origin` happens to point at if it were ever
# reconfigured, accidentally or otherwise.
EXPECTED_REMOTE='git@github.com:volodymyrkhrystynych/Lunaschal.git'
ACTUAL_REMOTE=$(git remote get-url origin)
if [ "$ACTUAL_REMOTE" != "$EXPECTED_REMOTE" ]; then
  echo "$(date -Iseconds) deploy-check: origin is '$ACTUAL_REMOTE', expected '$EXPECTED_REMOTE' — refusing to auto-pull" >&2
  exit 1
fi

# Under .git/, so it is never dirt in the tree and never in a backup.
STATE_DIR="$(git rev-parse --absolute-git-dir)/lunaschal-deploy"
mkdir -p "$STATE_DIR"
DEPLOYED=$(cat "$STATE_DIR/deployed" 2>/dev/null || true)
FAILED=$(cat "$STATE_DIR/failed" 2>/dev/null || true)

# The two values run-prod.sh takes from .env, read without exporting the rest
# of .env into npm and the smoke boot.
read -r LIVE_DB PORT < <(
  set +u -a
  [ -f .env ] && . ./.env
  echo "${DATABASE_URL:-./data/lunaschal.db} ${LUNASCHAL_PORT:-5000}"
)
case "$LIVE_DB" in
  /*) ;;
  *) LIVE_DB="$REPO/${LIVE_DB#./}" ;;
esac

git fetch origin main --quiet

LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse origin/main)
BRANCH=$(git branch --show-current)

# Only *tracked* edits count as dirt. Untracked files are not work-in-progress
# this watcher could destroy -- a --ff-only merge that would clobber one aborts
# on its own, and `set -e` turns that into a loud failure rather than a bad
# deploy. Counting them stalled production for hours behind a handful of stray
# 0-byte files in the repo root: every tick logged `skip-dirty` and exited 0.
DIRTY_FLAG=()
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  DIRTY_FLAG=(--dirty)
fi

# origin/main already contained in HEAD => a local commit that hasn't been
# pushed, not new upstream work. Without this the shas merely "differ" and every
# tick pulls nothing but still rebuilds and restarts the app — see deploy.py.
AHEAD_FLAG=()
if git merge-base --is-ancestor origin/main HEAD; then
  AHEAD_FLAG=(--local-ahead)
fi

DECISION=$(.venv/bin/python -m backend.ops.deploy --branch "$BRANCH" \
  --local "$LOCAL_SHA" --remote "$REMOTE_SHA" \
  --deployed "$DEPLOYED" --failed "$FAILED" \
  ${DIRTY_FLAG[@]+"${DIRTY_FLAG[@]}"} ${AHEAD_FLAG[@]+"${AHEAD_FLAG[@]}"}) || true
echo "$(date -Iseconds) deploy-check: $DECISION (branch=$BRANCH local=${LOCAL_SHA:0:8} remote=${REMOTE_SHA:0:8} deployed=${DEPLOYED:0:8})"

case "$DECISION" in
  deploy|finish) ;;
  skip-failed)
    # Non-zero so the unit shows as failed (and errors in Settings → Logs) for
    # as long as main stays broken, instead of a green timer hiding it.
    echo "deploy-check: ${REMOTE_SHA:0:8} already failed to start; waiting for a new commit on origin/main" >&2
    exit 1 ;;
  *) exit 0 ;;
esac

TARGET=$REMOTE_SHA
# What to come back to if TARGET won't run: the last sha seen healthy, else
# whatever was checked out (the first deploy after installing this watcher).
PREV=${DEPLOYED:-$LOCAL_SHA}

mark_failed() {
  echo "$TARGET" > "$STATE_DIR/failed"
  echo "$(date -Iseconds) deploy-check: FAILED ${TARGET:0:8} — $1" >&2
}

# Dependencies for moving the checkout from $1 to $2. The diff is against the
# last *deployed* sha, not the pre-pull HEAD, so a step that failed on an
# earlier tick is redone on the retry instead of being forgotten.
install_deps() {
  local changed
  changed=$(git diff --name-only "$1" "$2")
  if echo "$changed" | grep -qE '^requirements\.txt$'; then
    echo "Python requirements changed — reinstalling"
    .venv/bin/pip install -r requirements.txt
  fi
  if echo "$changed" | grep -qE '^(package\.json|package-lock\.json)$'; then
    echo "Dependency manifest changed — running npm ci"
    npm ci
  fi
}

# Vite empties its outDir before writing, so building straight into dist/
# left the live server serving a missing index.html for the length of the
# build — and an empty one for good if the build failed.
build_dist() {
  echo "Building..."
  rm -rf dist.next dist.prev
  npm run build -- --outDir dist.next --emptyOutDir
  [ -d dist ] && mv dist dist.prev
  mv dist.next dist
  rm -rf dist.prev
}

healthy() {
  for _ in $(seq 1 45); do
    if curl -skf -m 3 "https://127.0.0.1:$PORT/api/health" >/dev/null \
      || curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

# 1. Boot TARGET before it touches the live checkout. The venv is the live one,
#    so new requirements have to be in it first; installing them is additive
#    and the running server keeps the modules it already imported.
CANDIDATE="$(mktemp -d)/candidate"
cleanup() {
  git worktree remove --force "$CANDIDATE" 2>/dev/null || true
  rm -rf "$(dirname "$CANDIDATE")"
}
trap cleanup EXIT
git worktree add --quiet --detach "$CANDIDATE" "$TARGET"
if git diff --name-only "$PREV" "$TARGET" | grep -qE '^requirements\.txt$'; then
  echo "Python requirements changed — reinstalling before the smoke boot"
  .venv/bin/pip install -r "$CANDIDATE/requirements.txt"
fi
echo "Smoke-booting ${TARGET:0:8} against a copy of $LIVE_DB..."
if ! "$REPO/.venv/bin/python" "$REPO/backend/ops/smoke.py" --checkout "$CANDIDATE" --db "$LIVE_DB"; then
  mark_failed "it cannot start (smoke boot above); production left on ${LOCAL_SHA:0:8}"
  exit 1
fi

# 2. Move the live checkout and build.
if [ "$LOCAL_SHA" != "$TARGET" ]; then
  git merge --ff-only --quiet "$TARGET"
fi
install_deps "$PREV" "$TARGET"
build_dist

# 3. Restart and wait for it to answer.
echo "Restarting lunaschal.service..."
systemctl --user restart lunaschal.service
if healthy; then
  echo "$TARGET" > "$STATE_DIR/deployed"
  rm -f "$STATE_DIR/failed"
  echo "$(date -Iseconds) deploy-check: deployed ${PREV:0:8} -> ${TARGET:0:8}"
  exit 0
fi

mark_failed "no answer on :$PORT/api/health after restart"
journalctl --user -u lunaschal.service -n 40 --no-pager >&2 || true
if [ "$PREV" = "$TARGET" ]; then
  echo "deploy-check: nothing to roll back to" >&2
  exit 1
fi
echo "Rolling back to ${PREV:0:8}..."
git reset --hard --quiet "$PREV"
install_deps "$TARGET" "$PREV"
build_dist
systemctl --user restart lunaschal.service
if healthy; then
  echo "$(date -Iseconds) deploy-check: rolled back to ${PREV:0:8}" >&2
else
  echo "$(date -Iseconds) deploy-check: rollback to ${PREV:0:8} is not answering either" >&2
fi
exit 1
