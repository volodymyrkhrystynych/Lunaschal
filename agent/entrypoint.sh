#!/bin/sh
# Prepares a task's container, then idles: the agent and every command after it
# arrive through `docker exec`. Runs as the host user's uid on a read-only root,
# so it only writes to /work, $HOME and /tmp.
set -eu

# The model: the agents speak to http://127.0.0.1:8080, socat forwards that to
# the proxy's Unix socket mounted from the host. With --network none this is
# the only way out of the container, and it only reaches the proxy.
socat TCP-LISTEN:8080,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/run/lunaschal/model.sock &

# Agent configs, seeded once per task home so an agent's own edits survive
# between phases.
seed() { [ -e "$2" ] || { mkdir -p "$(dirname "$2")"; cp -R "$1" "$2"; }; }
seed /opt/agent-config/pi "$HOME/.pi/agent"
seed /opt/agent-config/omp "$HOME/.omp/agent"
seed /opt/agent-config/qwen "$HOME/.qwen"

# Except the files that are ours rather than the agent's: refreshed on every
# start, or a task created before an image change would keep the old model
# settings — and never see the lookup extension — for the rest of its life.
# Neither agent writes these over ACP.
refresh() { mkdir -p "$(dirname "$2")"; cp "$1" "$2"; }
for f in models.json settings.json extensions/lunaschal.js; do
  refresh "/opt/agent-config/pi/$f" "$HOME/.pi/agent/$f"
done
for f in models.yml config.yml; do
  refresh "/opt/agent-config/omp/$f" "$HOME/.omp/agent/$f"
done

# Where the image's node_modules/.vite-temp and .vite symlinks point (the
# Dockerfile says why). /tmp is a fresh tmpfs on every start.
mkdir -p /tmp/vite-temp /tmp/vite-cache

# Installed dependencies, linked into the clone. The repo's .gitignore says
# `node_modules/` and `.venv/` — patterns with a trailing slash match only
# directories, never these symlinks — so they go in info/exclude, or the
# agent's first `git add -A` commits them.
if [ -d /work/.git ]; then
  [ -e /work/node_modules ] || ln -s /opt/app/node_modules /work/node_modules
  [ -e /work/.venv ] || ln -s /opt/venv /work/.venv
  for p in /node_modules /.venv; do
    grep -qxF "$p" /work/.git/info/exclude 2>/dev/null || echo "$p" >> /work/.git/info/exclude
  done
fi

exec "$@"
