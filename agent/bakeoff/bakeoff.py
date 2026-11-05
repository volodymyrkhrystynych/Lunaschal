"""Head-to-head: pi vs omp vs Qwen Code on this repo's own recent fixes.

    .venv/bin/python agent/bakeoff/bakeoff.py --repo lunaschal --commits 5 \\
        --harnesses pi,omp,qwen --out data/agent/bakeoff.json

For each chosen commit, every agent gets the same job: start from the commit's
parent with *only the commit's test changes* applied, read the commit message
as the task, and make those tests pass. The plan is approved automatically.
The score is whether the tests pass when Lunaschal runs them afterwards, plus
wall time, tokens, and how often the proxy had to rescue a tool call.

A commit qualifies when it touches at least one test file and one non-test
file, is not a merge, and changes at most --max-lines lines. Pass --sha to
pick commits by hand instead.

Runs on the server (Docker + llama-server + the image). Expect hours: it is
meant to run overnight, and each result is written as soon as it exists, so a
stopped run keeps what it finished.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TEST_FILE = re.compile(r'(^|/)(test_[^/]+\.py|[^/]+\.test\.tsx?|[^/]+Tests?\.swift)$')

TASK_TEMPLATE = """{message}

The tests for this change have already been written and committed; they fail \
right now. Make them pass without weakening them, and keep the rest of the \
suite passing."""


def git(*args, cwd) -> str:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def candidates(mirror: Path, branch: str, max_lines: int, limit: int) -> list[str]:
    out = []
    for sha in git('log', '--no-merges', '--format=%H', '-n', '400', branch, cwd=mirror).split():
        stat = git('show', '--numstat', '--format=', sha, cwd=mirror).splitlines()
        files = [line.split('\t') for line in stat if line.count('\t') == 2]
        if not files:
            continue
        lines = sum(int(a) + int(r) for a, r, _ in files if a.isdigit() and r.isdigit())
        tests = [p for _, _, p in files if TEST_FILE.search(p)]
        code = [p for _, _, p in files if not TEST_FILE.search(p)]
        if tests and code and lines <= max_lines:
            out.append(sha)
        if len(out) >= limit:
            break
    return out


def prepare(task_id: str, mirror_path: Path, sha: str) -> None:
    """Workspace at the parent, with only the commit's test files applied and
    committed — on the host, before any agent has touched the clone, so the
    host running git there is still safe."""
    from backend.codeagent import mirror, tasks
    parent = git('rev-parse', f'{sha}^', cwd=mirror_path)
    mirror.create_workspace(task_id, mirror_path, start_point=parent)
    work = mirror.work_dir(task_id)
    files = [p for p in git('show', '--name-only', '--format=', sha, cwd=mirror_path).splitlines()
             if TEST_FILE.search(p)]
    patch = subprocess.run(['git', 'diff', parent, sha, '--', *files], cwd=mirror_path,
                           check=True, capture_output=True).stdout
    subprocess.run(['git', 'apply', '--index'], cwd=work, input=patch, check=True)
    git('commit', '-q', '-m', f'bakeoff: tests from {sha[:10]}', cwd=work)
    base = git('rev-parse', 'HEAD', cwd=work)
    tasks.update(task_id, base_sha=base)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='')
    ap.add_argument('--harnesses', default='pi,omp,qwen')
    ap.add_argument('--commits', type=int, default=5)
    ap.add_argument('--sha', action='append', default=[])
    ap.add_argument('--max-lines', type=int, default=150)
    ap.add_argument('--out', default='data/agent/bakeoff.json')
    args = ap.parse_args(argv)

    from backend.codeagent import mirror, tasks, worker
    from backend.codeagent.run import make_driver, run_phases, stats
    from backend.db.connection import init_db
    from backend.repos import registry
    init_db()
    repo = registry.get_by_slug(args.repo) if args.repo else registry.default_repo()
    mirror_path = mirror.sync_mirror(repo['slug'], repo['remoteUrl'])
    branch = repo.get('branch') or mirror.default_branch(mirror_path)
    shas = args.sha or candidates(mirror_path, branch, args.max_lines, args.commits)
    print(f'{len(shas)} commits: {", ".join(s[:10] for s in shas)}', flush=True)

    driver = make_driver()
    worker.configure(driver, driver.runtime)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(out_path.read_text()) if out_path.exists() else []
    done = {(r['sha'], r['harness']) for r in results}

    for sha in shas:
        message = git('log', '-1', '--format=%B', sha, cwd=mirror_path)
        for harness in args.harnesses.split(','):
            if (sha, harness) in done:
                continue
            task = tasks.create(repo['id'], TASK_TEMPLATE.format(message=message),
                                harness=harness, base_branch=branch)
            started = time.monotonic()
            try:
                prepare(task['id'], mirror_path, sha)
                final = run_phases(task['id'], approve=True, driver=driver)
            except Exception as e:  # recorded, and the run moves on
                final = {**tasks.get(task['id']), 'status': 'error', 'error': str(e)}
            finally:
                worker.cleanup(task['id'])
            row = {
                'sha': sha, 'harness': harness, 'status': final['status'],
                'tests_passed': final.get('testExit') == 0,
                'seconds': round(time.monotonic() - started),
                'files': len(final.get('diffStat') or []),
                'error': final.get('error'), **stats(task['id']),
            }
            results.append(row)
            out_path.write_text(json.dumps(results, indent=2))
            print(json.dumps(row), flush=True)

    by = {}
    for r in results:
        h = by.setdefault(r['harness'], {'runs': 0, 'passed': 0, 'seconds': 0, 'stuck': 0,
                                         'promoted': 0, 'poked': 0})
        h['runs'] += 1
        h['passed'] += r['tests_passed']
        h['seconds'] += r['seconds']
        for k in ('stuck', 'promoted', 'poked'):
            h[k] += r.get(k, 0)
    print('\n' + json.dumps(by, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
