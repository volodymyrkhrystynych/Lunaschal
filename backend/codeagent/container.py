"""The agent's container: how it is started, entered and removed.

The isolation is in `run_args`, and every flag there is load-bearing:

- `--network none` — no interface but loopback, so no outbound traffic by
  construction. The model is reached through a Unix socket bind-mounted from
  the host (the proxy listens on it); an in-container socat maps
  127.0.0.1:8080 onto it, so the agent's config can name an ordinary URL.
- `--read-only` with a sized tmpfs at /tmp — the image cannot be modified, and
  scratch space is bounded.
- `--cap-drop ALL`, `no-new-privileges`, a non-root `--user` matching the
  host's uid (so the files it writes in the mounted work dir stay ours to
  delete), and pid/memory/cpu limits.
- Exactly three mounts: the work dir, the agent's home, the socket dir.
  **Never the Docker socket** — access to it is root on the host.

The argument builders are pure so the policy is testable; everything that runs
Docker goes through `_run_docker`, the one seam tests replace.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.codeagent import mirror

IMAGE_REPO = 'lunaschal-agent'
CONTAINER_PREFIX = 'lunaschal-agent-'
MODEL_SOCKET = '/run/lunaschal/model.sock'

# The inputs that change what is installed in the image. The tag is their hash,
# so a dependency bump produces a new tag and the worker rebuilds.
IMAGE_INPUTS = ('agent/Dockerfile', 'agent/entrypoint.sh', 'agent/config',
                'requirements-agent.txt', 'package.json', 'package-lock.json')

DOCKER_TIMEOUT = 120
BUILD_TIMEOUT = 3600


@dataclass(frozen=True)
class Limits:
    memory: str = '16g'
    cpus: str = '6'
    pids: int = 1024
    tmp: str = '8g'


class DockerError(RuntimeError):
    pass


def container_name(task_id: str) -> str:
    mirror.branch_for(task_id)  # validates
    return CONTAINER_PREFIX + task_id.lower()


def image_tag(repo_root: Path) -> str:
    h = hashlib.sha256()
    for rel in IMAGE_INPUTS:
        p = repo_root / rel
        files = sorted(p.rglob('*')) if p.is_dir() else [p]
        for f in files:
            if f.is_file():
                h.update(str(f.relative_to(repo_root)).encode())
                h.update(f.read_bytes())
    return f'{IMAGE_REPO}:{h.hexdigest()[:12]}'


def build_args(repo_root: Path, tag: str) -> list[str]:
    return ['docker', 'build', '--quiet', '-t', tag, '-t', f'{IMAGE_REPO}:latest',
            '-f', str(repo_root / 'agent' / 'Dockerfile'), str(repo_root)]


def run_args(task_id: str, image: str, limits: Limits = Limits(),
             uid: int | None = None, gid: int | None = None) -> list[str]:
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    work = mirror.work_dir(task_id)
    home = mirror.home_dir(task_id)
    sock = mirror.sock_dir(task_id)
    return [
        'docker', 'run', '--detach',
        '--name', container_name(task_id),
        '--label', f'lunaschal.task={task_id}',
        '--network', 'none',
        '--read-only',
        '--tmpfs', f'/tmp:rw,nosuid,size={limits.tmp}',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges',
        '--pids-limit', str(limits.pids),
        '--memory', limits.memory,
        '--cpus', limits.cpus,
        '--user', f'{uid}:{gid}',
        '--env', 'HOME=/home/agent',
        '--workdir', '/work',
        '--mount', f'type=bind,src={work},dst=/work',
        '--mount', f'type=bind,src={home},dst=/home/agent',
        '--mount', f'type=bind,src={sock},dst={os.path.dirname(MODEL_SOCKET)}',
        image,
    ]


def exec_args(task_id: str, command: list[str], interactive: bool = False) -> list[str]:
    args = ['docker', 'exec']
    if interactive:
        args.append('-i')
    return [*args, '--workdir', '/work', container_name(task_id), *command]


def _run_docker(args: list[str], timeout: int = DOCKER_TIMEOUT,
                stdin: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=stdin, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise DockerError('Docker is not installed on this machine')
    except subprocess.TimeoutExpired:
        raise DockerError(f'docker {args[1]} timed out after {timeout}s')


def _check(proc: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if proc.returncode != 0:
        err = (proc.stderr or b'').decode(errors='replace').strip().splitlines()
        raise DockerError(f'{what} failed: {err[-1] if err else proc.returncode}')
    return proc


def image_exists(tag: str) -> bool:
    return _run_docker(['docker', 'image', 'inspect', tag]).returncode == 0


def ensure_image(repo_root: Path) -> str:
    tag = image_tag(repo_root)
    if not image_exists(tag):
        _check(_run_docker(build_args(repo_root, tag), timeout=BUILD_TIMEOUT), 'docker build')
    return tag


def start(task_id: str, image: str, limits: Limits = Limits()) -> None:
    remove(task_id)
    _check(_run_docker(run_args(task_id, image, limits)), 'docker run')


def is_running(task_id: str) -> bool:
    proc = _run_docker(['docker', 'inspect', '-f', '{{.State.Running}}',
                        container_name(task_id)])
    return proc.returncode == 0 and proc.stdout.strip() == b'true'


def remove(task_id: str) -> None:
    _run_docker(['docker', 'rm', '-f', container_name(task_id)])


def run_in(task_id: str, command: list[str], timeout: int = DOCKER_TIMEOUT,
           max_bytes: int | None = None) -> subprocess.CompletedProcess:
    """Run a command in the container and capture its output."""
    proc = _run_docker(exec_args(task_id, command), timeout=timeout)
    if max_bytes is not None and len(proc.stdout or b'') > max_bytes:
        raise DockerError('command output exceeded its size limit')
    return proc


def spawn(task_id: str, command: list[str]) -> subprocess.Popen:
    """Start a long-lived process in the container with piped stdio — the
    agent's ACP connection."""
    return subprocess.Popen(exec_args(task_id, command, interactive=True),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)


def list_task_containers() -> list[str]:
    proc = _run_docker(['docker', 'ps', '-a', '--filter', 'label=lunaschal.task',
                        '--format', '{{.Label "lunaschal.task"}}'])
    if proc.returncode != 0:
        return []
    return [t for t in proc.stdout.decode().split() if mirror.is_task_id(t)]
