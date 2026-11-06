"""The container's isolation policy, read off the argument lists."""
import subprocess

import pytest
from ulid import ULID

from backend.codeagent import container, mirror


@pytest.fixture(autouse=True)
def roots(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_ROOT', str(tmp_path / 'agent'))


@pytest.fixture
def task_id():
    return str(ULID())


def pairs(args):
    """Flag -> list of values, for flags that take one."""
    out = {}
    for flag, value in zip(args, args[1:]):
        if flag.startswith('--'):
            out.setdefault(flag, []).append(value)
    return out


def test_run_is_sealed(task_id):
    args = container.run_args(task_id, 'lunaschal-agent:abc', uid=1000, gid=1000)
    p = pairs(args)
    assert p['--network'] == ['none']
    assert '--read-only' in args
    assert p['--cap-drop'] == ['ALL']
    assert p['--security-opt'] == ['no-new-privileges']
    assert p['--user'] == ['1000:1000']
    assert '--privileged' not in args
    assert args[-1] == 'lunaschal-agent:abc'


def test_exactly_three_mounts_and_never_the_docker_socket(task_id):
    args = container.run_args(task_id, 'img')
    mounts = pairs(args)['--mount']
    assert [m.split(',dst=')[1] for m in mounts] == ['/work', '/home/agent', '/run/lunaschal']
    assert str(mirror.work_dir(task_id)) in mounts[0]
    joined = ' '.join(args)
    assert 'docker.sock' not in joined
    assert '--volume' not in args and '-v' not in args


def test_limits_are_applied(task_id):
    args = container.run_args(task_id, 'img', container.Limits(memory='4g', cpus='2', pids=99, tmp='1g'))
    p = pairs(args)
    assert p['--memory'] == ['4g'] and p['--cpus'] == ['2'] and p['--pids-limit'] == ['99']
    assert p['--tmpfs'] == ['/tmp:rw,nosuid,size=1g']


def test_exec_targets_the_tasks_container(task_id):
    args = container.exec_args(task_id, ['git', 'status'], interactive=True)
    assert args[:3] == ['docker', 'exec', '-i']
    assert args[-3:] == [container.container_name(task_id), 'git', 'status']


def test_container_names_are_validated():
    with pytest.raises(ValueError):
        container.container_name('x; rm -rf /')


def test_image_tag_follows_its_inputs(tmp_path):
    (tmp_path / 'agent').mkdir()
    (tmp_path / 'agent' / 'Dockerfile').write_text('FROM debian\n')
    (tmp_path / 'requirements-agent.txt').write_text('pytest\n')
    first = container.image_tag(tmp_path)
    assert first == container.image_tag(tmp_path)
    (tmp_path / 'requirements-agent.txt').write_text('pytest\nflask\n')
    assert container.image_tag(tmp_path) != first
    assert first.startswith('lunaschal-agent:')


def test_missing_docker_is_a_clear_error(monkeypatch, task_id):
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, 'run', boom)
    with pytest.raises(container.DockerError, match='not installed'):
        container.start(task_id, 'img')


def test_start_replaces_a_leftover_container(monkeypatch, task_id):
    calls = []

    def fake(args, timeout=0, stdin=None):
        calls.append(args[:2])
        return subprocess.CompletedProcess(args, 0, b'', b'')
    monkeypatch.setattr(container, '_run_docker', fake)
    container.start(task_id, 'img')
    assert calls == [['docker', 'rm'], ['docker', 'run']]


def test_list_task_containers_drops_junk_labels(monkeypatch, task_id):
    monkeypatch.setattr(container, '_run_docker', lambda *a, **k: subprocess.CompletedProcess(
        a, 0, f'{task_id}\nnot-an-id\n'.encode(), b''))
    assert container.list_task_containers() == [task_id]


# Left out of the agent image on purpose: the desktop window and the speech
# models. Everything else the app needs, the test suite may import.
_DESKTOP_AND_SPEECH = {'pywebview', 'qtpy', 'PyQt6', 'openai-whisper', 'kokoro-onnx', 'onnx-asr'}


def _requirements(name):
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    out = set()
    for line in (root / name).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '-r')):
            out.add(re.split(r'[<>=\[ ]', line, maxsplit=1)[0])
    return out


def test_agent_requirements_track_the_apps():
    app = _requirements('requirements.txt')
    agent = _requirements('requirements-agent.txt')
    assert app - _DESKTOP_AND_SPEECH <= agent, sorted(app - _DESKTOP_AND_SPEECH - agent)
    assert not agent & _DESKTOP_AND_SPEECH
    assert 'pytest' in agent


def test_image_inputs_exist():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for rel in container.IMAGE_INPUTS:
        assert (root / rel).exists(), rel
