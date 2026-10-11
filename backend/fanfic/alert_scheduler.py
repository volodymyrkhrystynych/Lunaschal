"""Poll account activity without walking every fic in the library."""
import logging
import os
import random
import threading
import time

from backend.db.connection import get_db
from backend.fanfic import alerts, browser, collections, download, pacing

MIN_INTERVAL = 30 * 60
MAX_INTERVAL = 90 * 60
_lock = threading.Lock()
_started = False
log = logging.getLogger(__name__)


def poll():
    result = alerts.refresh()
    for site, error in result['errors'].items():
        log.warning('Library alerts for %s: %s', site, error)
    # Recover the forum queue even if FF.net is paused or its scan fails.
    download.start_drain()
    # Keep discovery cheap for FF.net too: use the account lists and let the
    # existing persistent scans handle pagination, browser handoff and pacing.
    db = get_db()
    configured = download._cookie_for(pacing.DOMAIN) or browser.enabled()
    if configured and pacing.ready():
        active = db.execute("SELECT 1 FROM fanfic_collection_scans WHERE site=?"
                            " AND status='pending' LIMIT 1",
                            (pacing.DOMAIN,)).fetchone()
        if not active:
            collections.create_scan(pacing.DOMAIN, 'all', '')
        collections.start_scans()
    return result


def start_scheduler():
    global _started
    if os.environ.get('LUNASCHAL_NO_SCHEDULERS'):
        return
    with _lock:
        if _started:
            return
        _started = True

    def worker():
        # Give startup recovery a moment; subsequent polls are 30–90 min apart.
        time.sleep(60)
        while True:
            try:
                poll()
            except Exception:
                log.exception('Library account update poll failed')
            time.sleep(random.uniform(MIN_INTERVAL, MAX_INTERVAL))

    threading.Thread(target=worker, name='fanfic-alerts', daemon=True).start()
