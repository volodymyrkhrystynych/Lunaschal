"""Durable page handoff to the user's browser, never an HTTP fallback.

The existing workers keep their chapter/scan checkpoints. A missing rendered
page defers that worker; the extension supplies it and wakes the same worker.
Responses remain cached for that operation until it commits its final state.
"""
import re
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from ulid import ULID

from backend.db.connection import get_db
from backend.fanfic import pacing, sites

LEASE_SECONDS = 180
MAX_HTML = 4 * 1024 * 1024
_lock = threading.RLock()


class BrowserConflict(ValueError):
    pass


def enabled():
    return pacing.state()['retrieval_mode'] == 'browser'


def valid_url(url):
    if not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and p.hostname == 'www.fanfiction.net'
                and not p.port and not p.username and not p.password
                and bool(re.fullmatch(r'/s/\d+/\d+/(?:[^/]*)?|/(?:favorites|alert)/story\.php', p.path)))
    except (TypeError, ValueError):
        return False


def same_page(expected, actual):
    if not valid_url(actual):
        return False
    a, b = urlparse(expected), urlparse(actual)
    if a.path.startswith('/s/'):
        return a.path.split('/')[2:4] == b.path.split('/')[2:4]
    return a.path == b.path and a.query == b.query


def set_mode(mode):
    if mode not in ('http', 'browser'):
        raise ValueError('Choose http or browser retrieval')
    with _lock:
        db = get_db()
        db.execute('INSERT INTO fanfic_site_limits(domain,retrieval_mode) VALUES (?,?)'
                   ' ON CONFLICT(domain) DO UPDATE SET retrieval_mode=excluded.retrieval_mode',
                   (pacing.DOMAIN, mode))
        if mode == 'http':
            db.execute('DELETE FROM fanfic_browser_requests')
        db.commit()


def status():
    s = pacing.state()
    row = get_db().execute("SELECT url,status,error FROM fanfic_browser_requests"
                           " WHERE status != 'ready' ORDER BY created_at,id LIMIT 1").fetchone()
    return {'mode': s['retrieval_mode'], 'paused': bool(s['paused']),
            'nextRequest': max(s['next_request'], s['cooldown_until']),
            'connected': bool(s['browser_client'] and s['browser_seen'] > time.time() - LEASE_SECONDS),
            'needsAttention': bool(row and row['status'] == 'blocked'),
            'message': row['error'] if row else None}


def runnable_sql(site_column):
    # The column is supplied only by our two queue implementations, never a
    # request. One outstanding browser page serializes FF.net across both.
    return (f" AND NOT EXISTS (SELECT 1 FROM fanfic_site_limits b WHERE b.domain={site_column}"
            " AND b.retrieval_mode='browser' AND (b.browser_seen<unixepoch()-180"
            " OR b.browser_client IS NULL OR EXISTS (SELECT 1 FROM fanfic_browser_requests"
            " WHERE status != 'ready'))) ")


def fetch(url, *, fic_id=None, scan_id=None):
    if not valid_url(url):
        raise ValueError('Unsupported FF.net browser URL')
    if (fic_id is None) == (scan_id is None):
        raise ValueError('A browser page must belong to one import')
    with _lock:
        db = get_db()
        db.execute('INSERT OR IGNORE INTO fanfic_browser_requests(id,fic_id,scan_id,url,created_at)'
                   ' VALUES (?,?,?,?,?)', (str(ULID()), fic_id, scan_id, url, int(time.time())))
        db.commit()
        row = db.execute('SELECT * FROM fanfic_browser_requests WHERE fic_id IS ? AND scan_id IS ? AND url=?',
                         (fic_id, scan_id, url)).fetchone()
        if row['status'] == 'ready':
            return SimpleNamespace(text=row['html'], url=row['final_url'])
    raise pacing.DeferredDownload('Waiting for the FF.net browser tab; saved progress is retained')


def clear(*, fic_id=None, scan_id=None):
    with _lock:
        get_db().execute('DELETE FROM fanfic_browser_requests WHERE fic_id=? OR scan_id=?', (fic_id, scan_id))
        get_db().commit()


def _client(client_id):
    if not isinstance(client_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', client_id):
        raise ValueError('Invalid browser connection identifier')
    s = pacing.state()
    if (s['browser_client'] and s['browser_client'] != client_id
            and s['browser_seen'] > time.time() - LEASE_SECONDS):
        raise BrowserConflict('Another browser control tab is connected. Disconnect it first.')
    db = get_db()
    db.execute('INSERT INTO fanfic_site_limits(domain,browser_client,browser_seen) VALUES (?,?,?)'
               ' ON CONFLICT(domain) DO UPDATE SET browser_client=excluded.browser_client,'
               ' browser_seen=excluded.browser_seen', (pacing.DOMAIN, client_id, time.time()))
    db.commit()


def disconnect(client_id):
    with _lock:
        db = get_db()
        db.execute('UPDATE fanfic_site_limits SET browser_seen=0,browser_client=NULL WHERE browser_client=?', (client_id,))
        db.execute('UPDATE fanfic_browser_requests SET lease_until=0 WHERE client_id=?', (client_id,))
        db.commit()


def connect(client_id):
    with _lock:
        _client(client_id)
        set_mode('browser')
        return status()


def poll(client_id):
    with _lock:
        _client(client_id)
        if not enabled():
            return {'request': None, 'browser': status()}
        db = get_db()
        row = db.execute("SELECT * FROM fanfic_browser_requests WHERE status != 'ready'"
                         ' ORDER BY created_at,id LIMIT 1').fetchone()
        job = None
        if row:
            if ((row['client_id'] == client_id and row['status'] in ('leased', 'blocked'))
                    or (row['status'] == 'blocked' and row['lease_until'] <= time.time())):
                db.execute('UPDATE fanfic_browser_requests SET client_id=?,lease_until=? WHERE id=?',
                           (client_id, time.time() + LEASE_SECONDS, row['id']))
                db.commit()
                job = {'id': row['id'], 'attemptId': row['attempt_id'], 'url': row['url'], 'navigate': False,
                       'needsAttention': row['status'] == 'blocked', 'message': row['error']}
            elif row['lease_until'] <= time.time():
                try:
                    pacing.before_request(row['url'], wait=False)
                except pacing.DeferredDownload:
                    pass
                else:
                    attempt_id = str(ULID())
                    db.execute("UPDATE fanfic_browser_requests SET status='leased',attempt_id=?,client_id=?,"
                               'lease_until=?,error=NULL WHERE id=?',
                               (attempt_id, client_id, time.time() + LEASE_SECONDS, row['id']))
                    db.commit()
                    job = {'id': row['id'], 'attemptId': attempt_id, 'url': row['url'], 'navigate': True, 'needsAttention': False}
        return {'request': job, 'browser': status()}


def submit(request_id, client_id, payload):
    with _lock:
        db = get_db()
        row = db.execute('SELECT * FROM fanfic_browser_requests WHERE id=?', (request_id,)).fetchone()
        if (not row or row['client_id'] != client_id or not enabled()
                or pacing.state()['browser_client'] != client_id
                or row['attempt_id'] != payload.get('attemptId')):
            raise BrowserConflict('This browser request is no longer assigned to this tab')
        if row['status'] == 'ready':
            return  # Lost HTTP acknowledgement: the saved reply is idempotent.
        if row['status'] not in ('leased', 'blocked'):
            raise BrowserConflict('This request is waiting for its next allowed attempt')
        kind = payload.get('kind')
        if kind not in ('page', 'challenge', 'login', 'error', 'rate_limit'):
            raise ValueError('Invalid browser response kind')
        if kind == 'rate_limit':
            retry = payload.get('retryAfter')
            if retry is not None and (not isinstance(retry, str) or len(retry) > 100):
                raise ValueError('Invalid Retry-After header')
            db.execute("UPDATE fanfic_browser_requests SET status='pending',client_id=NULL,lease_until=0 WHERE id=?",
                       (request_id,))
            db.commit()
            try:
                pacing.suspend(SimpleNamespace(headers={'Retry-After': retry}))
            except pacing.DeferredDownload:
                return
        if kind == 'page':
            html, url = payload.get('html'), payload.get('url')
            if not isinstance(html, str) or len(html.encode('utf-8')) > MAX_HTML:
                raise ValueError('Browser page is missing or too large')
            if not same_page(row['url'], url):
                raise ValueError('The browser returned a different FF.net page')
            soup = BeautifulSoup(html, 'html.parser')
            try:
                if soup.select_one('input[type=password]'):
                    raise ValueError('Sign in to FF.net in the download tab, then continue')
                ref = sites.parse_work_url(url)
                if ref:
                    sites.parse_ffn(html, ref)
                else:
                    sites.parse_collection(html, url)
            except ValueError as exc:
                kind, message = 'error', str(exc)
            else:
                db.execute("UPDATE fanfic_browser_requests SET status='ready',html=?,final_url=?,error=NULL WHERE id=?",
                           (html, url, request_id))
                db.commit()
                return
        else:
            message = {'challenge': 'Complete the browser challenge in the FF.net tab, then Continue.',
                       'login': 'Sign in to FF.net in the download tab, then Continue.',
                       'error': 'The FF.net tab could not load this page. Open it and retry manually.'}[kind]
        db.execute("UPDATE fanfic_browser_requests SET status='blocked',error=? WHERE id=?", (message, request_id))
        db.commit()


def retry(request_id, client_id):
    with _lock:
        db = get_db()
        row = db.execute('SELECT * FROM fanfic_browser_requests WHERE id=?', (request_id,)).fetchone()
        if not row or row['client_id'] != client_id or pacing.state()['browser_client'] != client_id:
            raise BrowserConflict('This request is no longer assigned to this browser')
        if row['status'] not in ('blocked', 'leased'):
            raise BrowserConflict('This request cannot be retried')
        db.execute("UPDATE fanfic_browser_requests SET status='pending',client_id=NULL,lease_until=0,error=NULL WHERE id=?",
                   (request_id,))
        db.commit()
