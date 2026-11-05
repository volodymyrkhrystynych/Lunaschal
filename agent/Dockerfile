# The Code tab's agent image. One container per task, started by
# backend/codeagent/container.py with --network none, a read-only root, no
# capabilities and the host user's uid — see docs/code-agent.md.
#
# Built from the repo root (the context needs requirements-agent.txt and the
# package files):  docker build -t lunaschal-agent -f agent/Dockerfile .
# The worker builds it itself, tagged by a hash of its inputs, whenever one of
# those inputs changes (container.IMAGE_INPUTS).
#
# Everything the agent could want from the network is installed here, because
# at run time there is no network at all. A task that needs a new dependency
# needs a new image.

FROM node:22.12.0-bookworm-slim

# git/ripgrep for the agent's tools, socat for the model socket, and the
# system libraries the backend test suite reaches: pango for weasyprint's
# lazy import, libsndfile for soundfile.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ripgrep socat ca-certificates curl unzip procps \
      libpango-1.0-0 libpangoft2-1.0-0 libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

# Python through uv, so the container runs the same 3.13 the server does
# rather than whatever Debian ships.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_NO_CACHE=1
COPY requirements-agent.txt /opt/requirements-agent.txt
RUN uv venv --python 3.13 /opt/venv \
 && uv pip install --python /opt/venv/bin/python -r /opt/requirements-agent.txt

# The frontend's dependencies, linked into /work at start (entrypoint.sh).
# --ignore-scripts: `prepare` installs git hooks, which needs a repo.
COPY package.json package-lock.json /opt/app/
RUN cd /opt/app && npm ci --ignore-scripts --no-audit --no-fund

# The three agents, pinned. omp runs on Bun rather than Node.
RUN npm install -g --no-audit --no-fund \
      bun@1.4.3 \
      @mariozechner/pi-coding-agent@0.73.1 \
      pi-acp@0.0.34 \
      @qwen-code/qwen-code@0.25.2 \
      @oh-my-pi/pi-coding-agent@18.12.2

COPY agent/config /opt/agent-config
COPY agent/entrypoint.sh /usr/local/bin/agent-entrypoint
RUN chmod 0755 /usr/local/bin/agent-entrypoint

# Qwen Code reads its OpenAI-compatible endpoint from the environment. The
# URL is socat's end of the model socket; the proxy rewrites the model name.
ENV PATH=/opt/venv/bin:$PATH \
    PYTEST_BIN=/opt/venv/bin/pytest \
    OPENAI_BASE_URL=http://127.0.0.1:8080/v1 \
    OPENAI_API_KEY=local \
    OPENAI_MODEL=local \
    PI_TELEMETRY=0 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

ENTRYPOINT ["/usr/local/bin/agent-entrypoint"]
CMD ["sleep", "infinity"]
