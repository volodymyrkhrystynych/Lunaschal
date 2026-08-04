"""Pure logic for the daily backup script — no rsync, no ssh, no filesystem
walking. `ops/backup.sh` shells out to this for the two parts worth testing:
a WAL-safe DB snapshot and rolling-retention pruning.
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta

DATE_FORMAT = '%Y-%m-%d'


def snapshot_db(src_path: str, dest_path: str) -> None:
    """Atomic, WAL-safe copy of a live SQLite DB via `VACUUM INTO`.

    Safe to run against a DB with an open WAL and concurrent writers — unlike a
    plain file copy, which can grab `lunaschal.db` mid-checkpoint and miss pages
    still sitting in `lunaschal.db-wal`.
    """
    conn = sqlite3.connect(src_path)
    try:
        conn.execute('VACUUM INTO ?', (dest_path,))
    finally:
        conn.close()


def prune_candidates(existing_dates: list[str], keep_days: int, today: date) -> list[str]:
    """Which dated snapshot directory names (YYYY-MM-DD) fall outside the
    rolling retention window and should be deleted.

    A directory is kept if its date is within the last `keep_days` days
    (inclusive of today), regardless of gaps — a missed day (drive unplugged,
    tablet asleep) never disqualifies the days around it.
    """
    cutoff = today - timedelta(days=keep_days - 1)
    result = []
    for raw in existing_dates:
        try:
            d = datetime.strptime(raw, DATE_FORMAT).date()
        except ValueError:
            continue
        if d < cutoff:
            result.append(raw)
    return result


def main() -> None:
    """CLI used by ops/backup.sh, which runs identically whether the listing
    of existing snapshot names came from a local `ls` or a remote one over ssh
    — the pure logic here doesn't care which.
    """
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)

    p_snapshot = sub.add_parser('snapshot')
    p_snapshot.add_argument('src')
    p_snapshot.add_argument('dest')

    p_prune = sub.add_parser('prune')
    p_prune.add_argument('--keep-days', type=int, required=True)
    p_prune.add_argument('existing', nargs='*')

    args = parser.parse_args()

    if args.command == 'snapshot':
        snapshot_db(args.src, args.dest)
    elif args.command == 'prune':
        for name in prune_candidates(args.existing, args.keep_days, date.today()):
            print(name)


if __name__ == '__main__':
    sys.exit(main())
