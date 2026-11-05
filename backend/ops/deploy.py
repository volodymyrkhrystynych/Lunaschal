"""Pure deploy-decision logic for the auto-deploy watcher — no git, no subprocess.

The desktop machine this runs on doubles as a dev box, so the watcher must never
act while a feature branch is checked out or the tree is dirty — only a clean
`main` gets auto-pulled and rebuilt. `ops/deploy-check.sh` gathers the git state
and calls this as a CLI to get the decision.

Two more inputs come from the watcher's own state, kept under `.git/` so it is
neither dirt nor in a backup:

- `deployed` — the sha the watcher last saw come up healthy. HEAD alone can't
  answer "is this running?": the pull moves HEAD before the build and restart,
  so a deploy that failed partway used to read as `up-to-date` on every tick
  after it and was never retried.
- `failed` — a sha that failed its pre-restart boot or its post-restart health
  check. Without it the watcher would roll back, see origin ahead again, and
  try the same broken commit every five minutes.
"""

import sys
from typing import Literal

Decision = Literal[
    'deploy', 'finish', 'skip-branch', 'skip-dirty', 'skip-failed', 'up-to-date', 'ahead'
]


def needs_deploy(
    branch: str,
    dirty: bool,
    local_sha: str,
    remote_sha: str,
    local_ahead: bool = False,
    deployed_sha: str | None = None,
    failed_sha: str | None = None,
) -> Decision:
    """`local_ahead` means origin/main is already an ancestor of HEAD — a local
    commit on main that hasn't been pushed yet.

    That case has to be told apart from being *behind* origin, even though both
    are "the two shas differ". Treating it as `deploy` makes every tick pull
    nothing, then still run the rebuild and `systemctl restart
    lunaschal.service` at the end of ops/deploy-check.sh — tearing down
    production every 5 minutes for as long as the commit stays unpushed.

    `finish` is HEAD already at origin but not yet seen running: a deploy that
    pulled and then died before its health check. It reuses the deploy path
    minus the pull.
    """
    if branch != 'main':
        return 'skip-branch'
    if local_sha == remote_sha:
        if not deployed_sha or deployed_sha == local_sha:
            return 'up-to-date'
        if local_sha == failed_sha:
            return 'skip-failed'
        # Building a dirty tree would ship whatever is half-edited in it.
        return 'skip-dirty' if dirty else 'finish'
    if local_ahead:
        return 'ahead'
    if dirty:
        return 'skip-dirty'
    if remote_sha == failed_sha:
        return 'skip-failed'
    return 'deploy'


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--branch', required=True)
    parser.add_argument('--dirty', action='store_true')
    parser.add_argument('--local', required=True)
    parser.add_argument('--remote', required=True)
    parser.add_argument('--local-ahead', action='store_true')
    parser.add_argument('--deployed', default='')
    parser.add_argument('--failed', default='')
    args = parser.parse_args()

    decision = needs_deploy(
        args.branch, args.dirty, args.local, args.remote, args.local_ahead,
        args.deployed or None, args.failed or None,
    )
    print(decision)
    sys.exit(0 if decision in ('deploy', 'finish') else 1)


if __name__ == '__main__':
    main()
