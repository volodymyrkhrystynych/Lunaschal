"""Pure deploy-decision logic for the auto-deploy watcher — no git, no subprocess.

The desktop machine this runs on doubles as a dev box, so the watcher must never
act while a feature branch is checked out or the tree is dirty — only a clean
`main` gets auto-pulled and rebuilt. `ops/deploy-check.sh` gathers the git state
and calls this as a CLI to get the decision.
"""

import sys
from typing import Literal

Decision = Literal['deploy', 'skip-branch', 'skip-dirty', 'up-to-date', 'ahead']


def needs_deploy(
    branch: str,
    dirty: bool,
    local_sha: str,
    remote_sha: str,
    local_ahead: bool = False,
) -> Decision:
    """`local_ahead` means origin/main is already an ancestor of HEAD — a local
    commit on main that hasn't been pushed yet.

    That case has to be told apart from being *behind* origin, even though both
    are "the two shas differ". Treating it as `deploy` makes every tick pull
    nothing, then still run the unconditional rebuild and
    `systemctl restart lunaschal.service` at the end of ops/deploy-check.sh —
    tearing down the production window every 5 minutes for as long as the commit
    stays unpushed.
    """
    if branch != 'main':
        return 'skip-branch'
    if local_sha == remote_sha:
        return 'up-to-date'
    if local_ahead:
        return 'ahead'
    if dirty:
        return 'skip-dirty'
    return 'deploy'


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--branch', required=True)
    parser.add_argument('--dirty', action='store_true')
    parser.add_argument('--local', required=True)
    parser.add_argument('--remote', required=True)
    parser.add_argument('--local-ahead', action='store_true')
    args = parser.parse_args()

    decision = needs_deploy(
        args.branch, args.dirty, args.local, args.remote, args.local_ahead
    )
    print(decision)
    sys.exit(0 if decision == 'deploy' else 1)


if __name__ == '__main__':
    main()
