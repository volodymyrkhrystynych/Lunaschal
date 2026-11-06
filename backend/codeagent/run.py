"""Run a coding task from the shell, without the phone.

    python -m backend.codeagent.run --repo lunaschal --harness pi "Add a test for X"
    python -m backend.codeagent.run --repo lunaschal --harness omp --approve "..."

Plans, prints the plan, and — with --approve — builds it and prints the result.
This is how the setup is checked on the server (it needs Docker, llama-server
and the image) and what agent/bakeoff/bakeoff.py drives. It uses the same
driver, proxy and container as the worker; the only difference is that it runs
in the foreground and approves its own plan when asked to.

Run it while the production server is up and it shares the GPU lane properly
only within this process: the service's admission is in-memory, so a chat
message in the *server* process does not preempt this one. Fine for a check;
for real work, use the Code tab.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from backend.codeagent import tasks

REPO_ROOT = Path(__file__).resolve().parents[2]


def stats(task_id: str) -> dict:
    """Totals from the proxy's event rows: what the model cost and what the
    proxy had to fix."""
    out = {'calls': 0, 'waited': 0.0, 'ran': 0.0, 'prompt_tokens': 0,
           'completion_tokens': 0, 'preempted': 0, 'promoted': 0, 'poked': 0, 'stuck': 0}
    for e in tasks.events(task_id, limit=100000):
        if e['kind'] != 'proxy':
            continue
        p = e['payload']
        action = p.get('action')
        if action == 'call':
            out['calls'] += 1
            out['waited'] += p.get('waited') or 0
            out['ran'] += p.get('ran') or 0
            usage = p.get('usage') or {}
            out['prompt_tokens'] += usage.get('prompt_tokens') or 0
            out['completion_tokens'] += usage.get('completion_tokens') or 0
        elif action in out:
            out[action] += 1
    out['waited'] = round(out['waited'], 1)
    out['ran'] = round(out['ran'], 1)
    return out


def make_driver():
    from backend.codeagent.driver import DockerRuntime, Driver
    return Driver(DockerRuntime(REPO_ROOT))


def run_phases(task_id: str, approve: bool, driver=None) -> dict:
    from backend.codeagent import worker
    driver = driver or make_driver()
    task = driver.run(task_id)
    if approve and task['status'] == 'awaiting_plan':
        worker.approve_plan(task_id)
        task = driver.run(task_id)
    return task


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('prompt')
    ap.add_argument('--repo', default='', help='registered repo slug (default: the default repo)')
    ap.add_argument('--harness', choices=['pi', 'omp', 'qwen'], default='pi')
    ap.add_argument('--approve', action='store_true', help='approve the plan and build it')
    ap.add_argument('--keep', action='store_true', help='keep the container and workspace')
    args = ap.parse_args(argv)

    from backend.codeagent import worker
    from backend.db.connection import init_db
    from backend.repos import registry
    init_db()
    repo = registry.get_by_slug(args.repo) if args.repo else registry.default_repo()
    if not repo:
        print(f'No registered repo {args.repo!r}', file=sys.stderr)
        return 2
    task = tasks.create(repo['id'], args.prompt, harness=args.harness,
                        base_branch=repo.get('branch') or '')
    driver = make_driver()
    worker.configure(driver, driver.runtime)
    started = time.monotonic()
    print(f'task {task["id"]} on {repo["slug"]} with {args.harness}', flush=True)
    try:
        task = run_phases(task['id'], args.approve, driver)
    finally:
        if not args.keep:
            worker.cleanup(task['id'])
    print(f'\nstatus: {task["status"]}  ({time.monotonic() - started:.0f}s)')
    if task.get('error'):
        print(f'error: {task["error"]}')
    print(f'\n--- plan ---\n{task.get("plan") or ""}')
    if task.get('summary'):
        print(f'\n--- summary ---\n{task["summary"]}')
    if task.get('diffStat'):
        print('\n--- files ---')
        for f in task['diffStat']:
            print(f"  +{f['added']} -{f['removed']}  {f['path']}")
    if task.get('testExit') is not None:
        print(f'\ntests: exit {task["testExit"]}')
    print('\n--- model ---\n' + json.dumps(stats(task['id']), indent=2))
    return 0 if task['status'] in ('awaiting_plan', 'done') else 1


if __name__ == '__main__':
    sys.exit(main())
