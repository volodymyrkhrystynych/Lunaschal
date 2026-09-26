"""Persistent FF.net request spacing and site-wide download suspension."""
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from backend.db.connection import get_db

DOMAIN = 'fanfiction.net'
INTERVAL = 600
_lock = threading.Lock()


class DeferredDownload(Exception):
    pass


def applies(url):
    return urlparse(url).hostname in (DOMAIN, 'www.' + DOMAIN, 'm.' + DOMAIN)


def state():
    row = get_db().execute('SELECT * FROM fanfic_site_limits WHERE domain=?', (DOMAIN,)).fetchone()
    return dict(row) if row else {'domain': DOMAIN, 'next_request': 0, 'cooldown_until': 0,
                                'strikes': 0, 'paused': 0, 'reason': None,
                                'request_interval': INTERVAL, 'retrieval_mode': 'http',
                                'browser_client': None, 'browser_seen': 0}


def set_interval(seconds):
    if type(seconds) is not int or not 15 <= seconds <= 86400:
        raise ValueError('Request interval must be a whole number of seconds between 15 and 86400')
    with _lock:
        s = state()
        # Recalculate the wait from the last reserved request. Server cooldowns
        # and pauses remain independent of the user's chosen interval.
        next_request = (s['next_request'] - s['request_interval'] + seconds
                        if s['next_request'] else 0)
        db = get_db()
        db.execute('INSERT INTO fanfic_site_limits(domain,request_interval,next_request) VALUES (?,?,?)'
                   ' ON CONFLICT(domain) DO UPDATE SET request_interval=excluded.request_interval,'
                   ' next_request=excluded.next_request', (DOMAIN, seconds, next_request))
        db.commit()


def ready():
    s = state()
    return not s['paused'] and s['cooldown_until'] <= time.time()


def before_request(url, *, wait_for=None, wait=True):
    if not applies(url):
        return
    # Called under download's fetch lock, including redirects and retries.
    while True:
        with _lock:
            s = state()
            now = time.time()
            if s['paused'] or s['cooldown_until'] > now:
                raise DeferredDownload(s['reason'] or 'FF.net downloads are paused')
            remaining = s['next_request'] - now
            if remaining <= 0:
                db = get_db()
                db.execute('INSERT INTO fanfic_site_limits(domain,next_request) VALUES (?,?)'
                           ' ON CONFLICT(domain) DO UPDATE SET next_request=excluded.next_request',
                           (DOMAIN, now + s['request_interval']))
                db.commit()
                return
            if not wait:
                raise DeferredDownload('Waiting for the next FF.net request time')
        # Recheck pauses promptly, including ones requested during the wait.
        (wait_for or time.sleep)(min(remaining, 1))


def pause():
    with _lock:
        db = get_db()
        db.execute('INSERT INTO fanfic_site_limits(domain,paused,reason) VALUES (?,1,?)'
                   ' ON CONFLICT(domain) DO UPDATE SET paused=1,'
                   ' reason=CASE WHEN fanfic_site_limits.paused=1 THEN fanfic_site_limits.reason'
                   ' ELSE excluded.reason END',
                   (DOMAIN, 'FF.net downloads paused by you.'))
        db.commit()


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
        until = max(s['cooldown_until'], now + max(s['request_interval'], delay))
        reason = ('FF.net requires a browser challenge. Open the site in Firefox, refresh your '
                  'saved session if needed, then resume downloads.' if challenge else
                  'FF.net rate limit: downloads will resume after the cooldown.')
        db = get_db()
        db.execute('INSERT INTO fanfic_site_limits(domain,cooldown_until,strikes,paused,reason)'
                   ' VALUES (?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET'
                   ' cooldown_until=excluded.cooldown_until,strikes=excluded.strikes,'
                   ' paused=excluded.paused,reason=excluded.reason',
                   (DOMAIN, s['cooldown_until'] if challenge else until, strikes,
                    int(challenge or s['paused']), s['reason'] if s['paused'] else reason))
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
