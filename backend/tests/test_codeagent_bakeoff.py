"""The head-to-head script's commit selection and workspace preparation."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from ulid import ULID

from backend.codeagent import mirror, tasks
from backend.repos import git as repo_git

ROOT = Path(__file__).resolve().parents[2]
ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_COMMITTER_NAME': 'T',
       'GIT_COMMITTER_EMAIL': 't@e', 'PATH': os.environ['PATH']}

spec = importlib.util.spec_from_file_location('bakeoff', ROOT / 'agent' / 'bakeoff' / 'bakeoff.py')
bakeoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bakeoff)


def commit(repo, files, message):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True, env=ENV)
    subprocess.run(['git', 'commit', '-qm', message], cwd=repo, check=True, env=ENV)
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True,
                          text=True, env=ENV).stdout.strip()


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_ROOT', str(tmp_path / 'agent'))
    monkeypatch.setattr(repo_git, 'assert_clone_url', lambda url: url)
    seed = tmp_path / 'seed'
    seed.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=seed, check=True, env=ENV)
    commit(seed, {'app.py': 'def f():\n    return 1\n'}, 'first')
    code_only = commit(seed, {'app.py': 'def f():\n    return 2\n'}, 'code only')
    fix = commit(seed, {'app.py': 'def f():\n    return 3\n',
                        'tests/test_app.py': 'from app import f\n\ndef test_f():\n    assert f() == 3\n'},
                 'Fix f to return 3')
    huge = commit(seed, {'big.py': 'x = 1\n' * 400, 'tests/test_big.py': 'def test(): pass\n'},
                  'huge')
    m = mirror.sync_mirror('demo', str(seed))
    return m, {'code_only': code_only, 'fix': fix, 'huge': huge}


def test_candidates_need_tests_and_code_and_a_small_diff(history):
    m, shas = history
    assert bakeoff.candidates(m, 'main', max_lines=150, limit=5) == [shas['fix']]


def test_prepare_applies_only_the_tests_at_the_parent(history):
    m, shas = history
    task = tasks.create('repo', 'x')
    bakeoff.prepare(task['id'], m, shas['fix'])
    work = mirror.work_dir(task['id'])
    assert (work / 'tests' / 'test_app.py').exists()
    assert 'return 2' in (work / 'app.py').read_text()   # the fix itself is not applied
    base = tasks.get(task['id'])['baseSha']
    log = subprocess.run(['git', 'log', '-1', '--format=%s', base], cwd=work,
                         capture_output=True, text=True).stdout.strip()
    assert log == f"bakeoff: tests from {shas['fix'][:10]}"
    assert not subprocess.run(['git', 'status', '--porcelain'], cwd=work,
                              capture_output=True, text=True).stdout.strip()


def test_start_point_must_be_a_sha(history):
    m, _ = history
    with pytest.raises(ValueError):
        mirror.create_workspace(str(ULID()), m, start_point='main; rm -rf /')
