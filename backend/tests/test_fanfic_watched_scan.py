"""Watched-threads archive scan tests. Network is monkeypatched to fixture
HTML and both the scan thread and the drain worker run synchronously,
mirroring test_fanfic_alerts.py's setup."""
from pathlib import Path

import pytest

from backend.fanfic import download

FIXTURES = Path(__file__).parent / 'fixtures' / 'fanfic'

SITE = 'forums.spacebattles.com'
WATCHED_P1 = f'https://{SITE}/watched/threads?unread=0'
WATCHED_P2 = f'https://{SITE}/watched/threads/page-2?unread=0'


class FakeResp:
    def __init__(self, text, url):
        self.text = text
        self.url = url


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    from backend.routes import fanfic as fanfic_routes

    monkeypatch.setenv('FANFIC_ROOT', str(tmp_path / 'fanfic'))
    monkeypatch.setattr(download, 'REQUEST_DELAY', 0)
    monkeypatch.setattr(fanfic_routes, '_start_watch_scan_bg', download.run_watched_scan)

    pages = {
        WATCHED_P1: (FIXTURES / 'watched_threads_p1.html').read_text(),
        WATCHED_P2: (FIXTURES / 'watched_threads_p2.html').read_text(),
    }
    log: list[str] = []

    def fetch(url):
        log.append(url)
        if url not in pages:
            raise RuntimeError(f'404 for {url}')
        return FakeResp(pages[url], url)

    monkeypatch.setattr(download, '_fetch', fetch)
    return {'pages': pages, 'log': log}


def _put_cookie(client, domain=SITE):
    resp = client.put('/api/fanfic/cookies', json={'domain': domain, 'cookie': 'xf_user=u1'})
    assert resp.status_code == 200


def _scan_state(client, domain=SITE):
    cookies = client.get('/api/fanfic/cookies').get_json()
    return next(c for c in cookies if c['domain'] == domain).get('watchedScan')


def test_scan_requires_cookie(client, fake_net):
    resp = client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert resp.status_code == 400
    assert 'cookie' in resp.get_json()['error'].lower()


def test_scan_rejects_unknown_domain(client, fake_net):
    _put_cookie(client)
    resp = client.post('/api/fanfic/scan-watched/example.com')
    assert resp.status_code == 400


def test_scan_imports_missing_fics_and_walks_all_pages(client, fake_net):
    _put_cookie(client)
    resp = client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert resp.status_code == 202, resp.get_json()

    # p1 has 3 threads, p2 has 1 resolvable thread (the second row has no
    # thread link and is skipped) — all 4 are new, so all get queued and then
    # imported by the (synchronous) drain worker.
    fics = client.get('/api/fanfic').get_json()
    assert len(fics) == 4
    thread_ids = {f['sourceUrl'].split('.')[-1].strip('/') for f in fics}
    assert thread_ids == {'12345', '67890', '11111', '22222'}

    state = _scan_state(client)
    assert state['found'] == 4
    assert state['imported'] == 4
    assert state['alreadyInLibrary'] == 0
    assert state['done'] is True
    assert state['error'] is None
    # a completed pass wraps back to page 1 so a later click rescans fresh
    assert state['page'] == 1


def test_scan_skips_fic_already_in_library(client, fake_net):
    resp = client.post('/api/fanfic/import', json={
        'url': f'https://{SITE}/threads/a-test-fic.12345/'})
    assert resp.status_code in (200, 202)

    _put_cookie(client)
    client.post(f'/api/fanfic/scan-watched/{SITE}')

    state = _scan_state(client)
    assert state['found'] == 4
    assert state['alreadyInLibrary'] == 1
    assert state['imported'] == 3


def test_scan_conflicts_while_active(client, fake_net, monkeypatch):
    from backend.routes import fanfic as fanfic_routes
    monkeypatch.setattr(fanfic_routes, '_start_watch_scan_bg', lambda domain: None)
    _put_cookie(client)

    with download._watch_scan_lock:
        download._watch_scan_progress[SITE] = {
            'page': 1, 'lastPage': None, 'found': 0, 'imported': 0,
            'alreadyInLibrary': 0, 'done': False, 'error': None,
        }
    try:
        resp = client.post(f'/api/fanfic/scan-watched/{SITE}')
        assert resp.status_code == 409
    finally:
        download._watch_scan_progress.pop(SITE, None)


def test_scan_resumes_from_checkpoint_after_interruption(client, fake_net, monkeypatch):
    _put_cookie(client)

    real_fetch = download._fetch

    def flaky_fetch(url):
        if url == WATCHED_P2:
            raise RuntimeError('simulated crash before page 2')
        return real_fetch(url)

    monkeypatch.setattr(download, '_fetch', flaky_fetch)
    resp = client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert resp.status_code == 202

    # page 1 was processed and checkpointed before the crash on page 2
    from backend.db.connection import get_db
    row = get_db().execute(
        'SELECT next_page, found, imported FROM fanfic_watched_scans WHERE domain=?',
        (SITE,)).fetchone()
    assert row['next_page'] == 2
    assert row['found'] == 3
    fics_after_p1 = client.get('/api/fanfic').get_json()
    assert len(fics_after_p1) == 3

    state = _scan_state(client)
    assert state['error'] is not None
    assert state['done'] is True

    monkeypatch.setattr(download, '_fetch', real_fetch)
    fake_net['log'].clear()
    resp = client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert resp.status_code == 202
    # resumed straight into page 2 — page 1 was not re-fetched
    assert WATCHED_P1 not in fake_net['log']
    assert WATCHED_P2 in fake_net['log']

    fics = client.get('/api/fanfic').get_json()
    assert len(fics) == 4
    state = _scan_state(client)
    assert state['error'] is None
    assert state['page'] == 1  # wrapped after finishing the last page
