"""Persistent FF.net request spacing and site-wide download suspension."""
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from backend.db.connection import get_db

DOMAIN = 'fanfiction.net'
INTERVAL = 15
_lock = threading.Lock()


class DeferredDownload(Exception):
    pass


def applies(url):
    return urlparse(url).hostname in (DOMAIN, 'www.' + DOMAIN, 'm.' + DOMAIN)


def state():
    row = get_db().execute('SELECT * FROM fanfic_site_limits WHERE domain=?', (DOMAIN,)).fetchone()
    return dict(row) if row else {'domain': DOMAIN, 'next_request': 0, 'cooldown_until': 0,
                                'strikes': 0, 'paused': 0, 'reason': None}


def ready():
    s = state()
    return not s['paused'] and s['cooldown_until'] <= time.time()


def before_request(url):
    if not applies(url):
        return
    # Called under download's fetch lock, including redirects and retries.
    while True:
        with _lock:
            s = state()
            now = time.time()
            if s['paused'] or s['cooldown_until'] > now:
                raise DeferredDownload(s['reason'] or 'FF.net downloads are paused')
            wait = s['next_request'] - now
            if wait <= 0:
                db = get_db()
                db.execute('INSERT INTO fanfic_site_limits(domain,next_request) VALUES (?,?)'
                           ' ON CONFLICT(domain) DO UPDATE SET next_request=excluded.next_request',
                           (DOMAIN, now + INTERVAL))
                db.commit()
                return
        time.sleep(min(wait, INTERVAL))


def retry_delay(value, now):
    try:
        if value and value.strip().isdigit():
            return int(value.strip())
        if value:
            return max(0, parsedate_to_datetime(value).timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def suspend(response, challenge=False):
    with _lock:
        s = state()
        now = time.time()
        strikes = min(s['strikes'] + 1, 8)
        delay = retry_delay(response.headers.get('Retry-After'), now)
        if delay is None:
            delay = min(900 * 2 ** (strikes - 1), 86400)
        until = max(s['cooldown_until'], now + max(INTERVAL, delay))
        reason = ('FF.net requires a browser challenge. Open the site in Firefox, refresh your '
                  'saved session if needed, then resume downloads.' if challenge else
                  'FF.net rate limit: downloads will resume after the cooldown.')
        db = get_db()
        db.execute('INSERT INTO fanfic_site_limits(domain,cooldown_until,strikes,paused,reason)'
                   ' VALUES (?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET'
                   ' cooldown_until=excluded.cooldown_until,strikes=excluded.strikes,'
                   ' paused=excluded.paused,reason=excluded.reason',
                   (DOMAIN, s['cooldown_until'] if challenge else until, strikes, int(challenge), reason))
        db.commit()
    raise DeferredDownload(reason)


def resume():
    # Manual resume clears a challenge, never an outstanding server cooldown.
    with _lock:
        db = get_db()
        db.execute('UPDATE fanfic_site_limits SET paused=0 WHERE domain=?', (DOMAIN,))
        db.commit()


_started = False


def start_scheduler():
    global _started
    import os
    if os.environ.get('LUNASCHAL_NO_SCHEDULERS'):
        return
    with _lock:
        if _started:
            return
        _started = True

    def worker():
        from backend.fanfic import collections, download
        while True:
            try:
                if ready():
                    db = get_db()
                    if db.execute("SELECT 1 FROM fanfic_collection_scans WHERE status='pending' LIMIT 1").fetchone():
                        collections.start_scans()
                    if db.execute('SELECT 1 FROM fics WHERE update_pending=1 LIMIT 1').fetchone():
                        download.start_drain()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Library cooldown recovery failed')
            time.sleep(5)
    threading.Thread(target=worker, daemon=True).start()
