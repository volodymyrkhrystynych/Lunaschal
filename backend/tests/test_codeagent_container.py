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


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split('.'))


def test_image_pins_a_pi_that_pi_acp_can_drive():
    """pi-acp 0.0.34 sends commands pi only grew in 0.81, and an older pi
    answers them with an id-less error that pi-acp waits on forever — every
    pi task then died with "session/new timed out". The pi package also moved
    from @mariozechner to @earendil-works on the way, and wants Node >= 22.19."""
    import re
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parents[2] / 'agent' / 'Dockerfile').read_text()
    assert '@mariozechner/pi-coding-agent' not in dockerfile
    pi = re.search(r'@earendil-works/pi-coding-agent@([\d.]+)', dockerfile)
    assert pi and _version(pi.group(1)) >= (0, 81, 0)
    assert re.search(r'pi-acp@0\.0\.34\b', dockerfile), 'recheck the pi minimum when bumping pi-acp'
    node = re.search(r'^FROM node:([\d.]+)', dockerfile, re.M)
    assert node and _version(node.group(1)) >= (22, 19, 0)


def _root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


def _ctx_size() -> int:
    """[qwen36]'s ctx-size: the one shared KV pool every slot may address."""
    import re
    text = (_root() / 'llama' / 'presets.ini').read_text()
    section = text.split('[qwen36]', 1)[1].split('\n[', 1)[0]
    return int(re.search(r'^ctx-size\s*=\s*(\d+)', section, re.M).group(1))


def test_the_agent_gets_the_whole_context_pool():
    """kv-unified lets one slot address the whole pool, so the agent's window
    is the server's ctx-size — and moves with it, or pi plans compaction
    against a window that no longer exists."""
    import json
    import re
    pi = json.loads((_root() / 'agent/config/pi/models.json').read_text())
    [model] = pi['providers']['lunaschal']['models']
    assert model['contextWindow'] == _ctx_size()
    omp = (_root() / 'agent/config/omp/models.yml').read_text()
    assert int(re.search(r'contextWindow:\s*(\d+)', omp).group(1)) == _ctx_size()


def test_pi_speaks_qwen_to_llama_server():
    """Without thinkingFormat pi sends OpenAI's reasoning_effort, which
    llama-server ignores: the thinking level silently did nothing. The budget
    goes out under the field that ends a think cleanly rather than
    mid-sentence, and the default level is the long overnight-sized one."""
    import json
    from backend.codeagent import proxy
    pi = json.loads((_root() / 'agent/config/pi/models.json').read_text())
    [model] = pi['providers']['lunaschal']['models']
    assert model['compat']['thinkingFormat'] == 'qwen-chat-template'
    assert model['compat']['thinkingTokenBudgetField'] == 'reasoning_budget_tokens'
    assert model['maxTokens'] == proxy.MIN_MAX_TOKENS
    settings = json.loads((_root() / 'agent/config/pi/settings.json').read_text())
    level = settings['defaultThinkingLevel']
    assert settings['thinkingBudgets'][level] == proxy.THINKING_BUDGET
    assert {'grep', 'find', 'ls'} <= set(settings['defaultTools'])


def test_the_context_window_constant_is_the_servers():
    from backend.ai import llm
    assert llm.CONTEXT_WINDOW_TOKENS == _ctx_size()


def test_every_harness_compacts_at_the_same_point_and_leaves_a_turn_free():
    """All three compact at COMPACT_AT of the window, the same point Chat
    does, and that leaves room for the proxy's longest turn — a full think
    plus its answer. Each harness spells it differently: pi as a reserve,
    omp as a percentage, Qwen Code as a fraction of a window it must be told."""
    import json
    import re
    from backend.ai import llm
    from backend.chat import compaction
    from backend.codeagent import proxy
    window = _ctx_size()
    trigger = int(window * llm.COMPACT_AT)
    assert window - trigger >= proxy.MIN_MAX_TOKENS
    assert compaction.SOFT_LIMIT_TOKENS == trigger

    pi = json.loads((_root() / 'agent/config/pi/settings.json').read_text())
    assert pi['compaction']['enabled'] is True
    assert window - pi['compaction']['reserveTokens'] == trigger
    assert pi['httpIdleTimeoutMs'] == 0

    omp = (_root() / 'agent/config/omp/config.yml').read_text()
    assert int(re.search(r'thresholdPercent:\s*(\d+)', omp).group(1)) == round(llm.COMPACT_AT * 100)
    omp_models = (_root() / 'agent/config/omp/models.yml').read_text()
    assert int(re.search(r'maxTokens:\s*(\d+)', omp_models).group(1)) == proxy.MIN_MAX_TOKENS

    qwen = json.loads((_root() / 'agent/config/qwen/settings.json').read_text())
    assert qwen['context']['autoCompactThreshold'] == llm.COMPACT_AT
    # Qwen Code guesses the window from the model's name, and "local" names
    # nothing: without this it compacts against a window that isn't ours.
    assert qwen['model']['generationConfig']['contextWindowSize'] == window


def test_qwen_code_does_not_abort_a_long_think():
    """A 48k think runs ~25 minutes. Qwen Code's defaults abort a request at 2
    minutes and a stream at 15; the proxy's keepalives cannot reset the second."""
    import json
    qwen = json.loads((_root() / 'agent/config/qwen/settings.json').read_text())
    assert qwen['model']['generationConfig']['timeout'] == 0
    assert 'QWEN_STREAM_MAX_LIFETIME_MS=0' in (_root() / 'agent/Dockerfile').read_text()


def test_entrypoint_refreshes_the_files_that_are_ours():
    """`seed` copies once per task home, so a task created before an image
    change would otherwise never see new model settings or the extension."""
    entry = (_root() / 'agent/entrypoint.sh').read_text()
    for f in ('models.json', 'settings.json', 'extensions/lunaschal.js'):
        assert f in entry
        assert (_root() / 'agent/config/pi' / f).is_file()
    for f in ('models.yml', 'config.yml'):
        assert f in entry
        assert (_root() / 'agent/config/omp' / f).is_file()


def test_nothing_but_extensions_sits_in_the_extensions_folder():
    """pi loads every file in it — a test file there would be loaded as an
    extension and fail on its vitest import."""
    folder = _root() / 'agent/config/pi/extensions'
    assert [p.name for p in folder.iterdir()] == ['lunaschal.js']
