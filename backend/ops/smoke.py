"""Boot a candidate checkout against a copy of the live database, before the
auto-deploy watcher lets it anywhere near production.

`ops/deploy-check.sh` runs the *live* checkout's copy of this file against a
throwaway worktree of the commit it is about to deploy:
`python backend/ops/smoke.py --checkout <worktree> --db <live db>`. The checker
is the one already running rather than the candidate's, so a commit that broke
or predates this file is still checked. Exit 0 means
`create_app()` came up and `/api/health` answered; anything else means the
restart would have produced a crash loop, so the watcher never pulls it.

That is exactly the outage it exists for: two PRs each added the same Flask
route, git merged both without a conflict, and `create_app()` raised on every
start — while the deploy logged success, because `systemctl restart` returns as
soon as the process has been exec'd. A boot is also the only place the
`_ensure_*` migrations meet real data rather than the test template, so they
run here first, on the copy.

Three things keep the boot from reaching outside the copy: the DB's
`prevent_sleep` is cleared (the inhibitor setup kills "orphaned" inhibitors,
which would include production's), the schedulers are off, and the process
works from a temp directory so every default `./data/...` root lands there.
"""

import os
import sys

# Run as a file, Python puts this directory first on sys.path; the candidate's
# `backend` package has to be the only one importable.
if sys.path and os.path.abspath(sys.path[0]) == os.path.dirname(os.path.abspath(__file__)):
    sys.path.pop(0)

import argparse
import sqlite3
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def copy_db(src: Path, dst: Path) -> None:
    """A consistent snapshot of a live WAL database, minus boot side effects."""
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        columns = {row[1] for row in target.execute('PRAGMA table_info(settings)')}
        if 'prevent_sleep' in columns:
            target.execute('UPDATE settings SET prevent_sleep=0')
            target.commit()
    finally:
        target.close()
        source.close()


def boot(checkout: Path, db_path: Path) -> None:
    """Raise if `checkout`'s app cannot start against `db_path`."""
    os.environ['DATABASE_URL'] = str(db_path)
    os.environ['LUNASCHAL_NO_SCHEDULERS'] = '1'
    for name in ('STT_LISTENER', 'NETWORK_MODE', 'FLASK_DEBUG'):
        os.environ.pop(name, None)
    sys.path.insert(0, str(checkout))
    from backend.app import create_app

    response = create_app().test_client().get('/api/health')
    if response.status_code != 200:
        raise RuntimeError(f'/api/health answered {response.status_code}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkout', type=Path, default=REPO,
                        help='the tree to boot; defaults to the one this file is in')
    parser.add_argument('--db', required=True, type=Path,
                        help='the live database; it is copied, never opened for writing')
    args = parser.parse_args()
    live = args.db.resolve()
    checkout = args.checkout.resolve()

    with tempfile.TemporaryDirectory(prefix='lunaschal-smoke-') as tmp:
        copy = Path(tmp) / 'smoke.db'
        if live.exists():
            copy_db(live, copy)
        os.chdir(tmp)
        try:
            boot(checkout, copy)
        except BaseException:
            traceback.print_exc()
            print('smoke: FAILED — this commit cannot start', file=sys.stderr)
            sys.exit(1)
    print('smoke: ok')


if __name__ == '__main__':
    main()
