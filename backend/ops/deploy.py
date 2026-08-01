"""Pure deploy-decision logic for the auto-deploy watcher — no git, no subprocess.

The desktop machine this runs on doubles as a dev box, so the watcher must never
act while a feature branch is checked out or the tree is dirty — only a clean
`main` gets auto-pulled and rebuilt. `ops/deploy-check.sh` gathers the git state
and calls this as a CLI to get the decision.
"""

import sys
from typing import Literal

Decision = Literal['deploy', 'skip-branch', 'skip-dirty', 'up-to-date']


def needs_deploy(branch: str, dirty: bool, local_sha: str, remote_sha: str) -> Decision:
    if branch != 'main':
        return 'skip-branch'
    if local_sha == remote_sha:
        return 'up-to-date'
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
    args = parser.parse_args()

    decision = needs_deploy(args.branch, args.dirty, args.local, args.remote)
    print(decision)
    sys.exit(0 if decision == 'deploy' else 1)


if __name__ == '__main__':
    main()
