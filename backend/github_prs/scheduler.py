"""Poll GitHub for newly merged PRs every 15 minutes.

Makes no model call itself — the summary is an `llm_jobs` row — so it keeps to
no window and needs no slot. A failed tick (GitHub down, rate limited) is just
retried on the next one; the ledger makes every tick idempotent.
"""
import logging
import os
import threading
import time

from backend.github_prs import sync

logger = logging.getLogger(__name__)

_POLL_SECONDS = 15 * 60


def _scheduler_loop() -> None:
    while True:
        try:
            added = sync.sync()
            if added:
                logger.info('PR journal: %d merged PR(s) filed', added)
        except Exception as e:
            print(f'PR journal sync failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_pr_journal_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
