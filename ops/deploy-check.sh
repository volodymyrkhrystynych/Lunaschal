#!/usr/bin/env bash
# Auto-deploy watcher, run periodically by lunaschal-deploy.timer. Only ever
# acts on a clean `main` checkout — the desktop this runs on doubles as a dev
# box, so a dirty tree or a feature branch checked out means "leave it alone,
# try again next tick" rather than force-switching branches or stashing.
#
# Decision logic (branch/dirty/up-to-date) lives in backend/ops/deploy.py so
# it's unit tested; this script only does the git/npm/systemctl orchestration.
set -e

cd "$(dirname "$0")/.."

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

git fetch origin main --quiet

LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse origin/main)
BRANCH=$(git branch --show-current)

DIRTY_FLAG=()
if [ -n "$(git status --porcelain)" ]; then
  DIRTY_FLAG=(--dirty)
fi

DECISION=$(.venv/bin/python -m backend.ops.deploy --branch "$BRANCH" --local "$LOCAL_SHA" --remote "$REMOTE_SHA" "${DIRTY_FLAG[@]}") || true
echo "$(date -Iseconds) deploy-check: $DECISION (branch=$BRANCH local=${LOCAL_SHA:0:8} remote=${REMOTE_SHA:0:8})"

if [ "$DECISION" != "deploy" ]; then
  exit 0
fi

git pull --ff-only origin main
NEW_SHA=$(git rev-parse HEAD)

CHANGED=$(git diff --name-only "$LOCAL_SHA" "$NEW_SHA")

if echo "$CHANGED" | grep -qE '^(package\.json|package-lock\.json)$'; then
  echo "Dependency manifest changed — running npm ci"
  npm ci
fi

if echo "$CHANGED" | grep -qE '^requirements\.txt$'; then
  echo "Python requirements changed — reinstalling"
  .venv/bin/pip install -r requirements.txt
fi

echo "Building..."
npm run build

echo "Restarting lunaschal.service..."
systemctl --user restart lunaschal.service

echo "$(date -Iseconds) deploy-check: deployed ${LOCAL_SHA:0:8} -> ${NEW_SHA:0:8}"
