"""Reconcile Lunaschal's torrent rows with the client, and purge aged-out ones.

Modelled on backend/jobs/scheduler.py: a pure `tick()` carrying its own
`last_purge_date` so the decision is testable without threads or a clock, and a
loop that never lets one bad pass kill it.

Needs no llama slot — nothing here calls a model — so it never consults
backend/ai/priority.py and sweeps on every tick. Only the destructive half keeps
to a window.
"""

import datetime
import logging
import os
import threading
import time

from backend.db.connection import get_db
from backend.torrent import client, merge, retention

logger = logging.getLogger(__name__)

# Hashes that were missing from the client on the *previous* pass. An orphan
# has to be missing twice in a row before its row is dropped, because
# qBittorrent answers `torrents/info` with an empty list for the first moment
# after it starts, before it has loaded its resume data — and the container
# restarts whenever the VPN one does. Acting on a single observation would
# delete every note the user has written, silently, on a routine restart.
# Costs one extra minute before a genuinely removed torrent is forgotten.
_previously_missing: set[str] = set()

# The client is on loopback and `torrents/info` is one cheap call, but nothing
# waits on this — the UI polls the client directly. This exists to stamp
# completions and tidy up, which can be a minute late.
_POLL_SECONDS = 60

# After the other four schedulers' windows (jobs' file purge owns 07:00–08:00),
# so a large delete never overlaps a backup or a model-using pass.
PURGE_WINDOW_START_HOUR = 8
PURGE_WINDOW_END_HOUR = 9


def _rows() -> list[dict]:
    return [dict(r) for r in get_db().execute('SELECT * FROM torrents')]


def reconcile_once() -> dict:
    """Stamp completions and forget torrents that left the client.

    `completed_at` is ours rather than read live because retention is measured
    from it and the client's `completion_on` is lost whenever its config is
    rebuilt — which is any time the container is recreated. Copying it once,
    when we first see it, makes the retention clock survive that.
    """
    live = client.torrents_info()
    rows = _rows()
    db = get_db()

    by_hash = {(t.get('hash') or '').lower(): t for t in live}
    completed = 0
    for row in rows:
        if row.get('completed_at'):
            continue
        torrent = by_hash.get((row.get('info_hash') or '').lower())
        if torrent is None:
            continue
        finished = torrent.get('completion_on')
        if isinstance(finished, int) and finished > 0:
            db.execute(
                'UPDATE torrents SET completed_at=?, updated_at=? WHERE id=?',
                (finished, int(time.time()), row['id']),
            )
            completed += 1

    global _previously_missing
    missing_now = set(merge.orphaned_hashes(live, rows))
    confirmed = missing_now & _previously_missing
    _previously_missing = missing_now
    for info_hash in confirmed:
        db.execute('DELETE FROM torrents WHERE info_hash=?', (info_hash,))

    if completed or confirmed:
        db.commit()
    return {'completed': completed, 'forgotten': len(confirmed)}


def run_purge_sweep() -> dict:
    """Delete torrents whose per-torrent retention has elapsed, files and all.

    Files go with them: a retention policy that keeps the bytes has not freed
    anything, which is the only reason to have one.
    """
    now = int(time.time())
    due = retention.torrents_to_purge(_rows(), now)
    db = get_db()
    purged = 0
    for row in due:
        try:
            client.delete(row['info_hash'], delete_files=True)
        except client.TorrentClientError as e:
            # Leave the row alone so the next sweep tries again. Marking it
            # purged here would strand the files with nothing tracking them.
            logger.warning('Torrent purge failed for %s: %s', row['info_hash'], e)
            continue
        db.execute('DELETE FROM torrents WHERE id=?', (row['id'],))
        purged += 1
    if purged:
        db.commit()
    return {'purged': purged}


def tick(*, now: datetime.datetime | None = None, last_purge_date=None):
    now = now or datetime.datetime.now()
    results: dict = {}

    try:
        results['reconcile'] = reconcile_once()
    except client.TorrentClientUnavailable:
        # The stack is simply not running. That is a normal state — the user
        # starts it when they want it — so it is not worth a log line every
        # minute. Nothing else in this tick can work either.
        return results, last_purge_date
    except Exception as e:
        logger.warning('Torrent reconcile failed: %s', e)

    in_window = PURGE_WINDOW_START_HOUR <= now.hour < PURGE_WINDOW_END_HOUR
    if in_window and last_purge_date != now.date():
        last_purge_date = now.date()
        try:
            results['purge'] = run_purge_sweep()
        except Exception as e:
            logger.warning('Torrent purge sweep failed: %s', e)

    return results, last_purge_date


def _scheduler_loop() -> None:
    last_purge_date = None
    while True:
        try:
            _, last_purge_date = tick(last_purge_date=last_purge_date)
        except Exception as e:
            logger.warning('Torrent scheduler tick failed: %s', e)
        time.sleep(_POLL_SECONDS)


def start_torrent_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
