"""Alerts-refresh + serial update-queue tests. Network is monkeypatched to
fixture HTML and the drain worker runs synchronously, mirroring
test_fanfic_import.py's setup."""
from pathlib import Path

import pytest

from backend.fanfic import download

FIXTURES = Path(__file__).parent / 'fixtures' / 'fanfic'

SITE = 'forums.spacebattles.com'
SV = 'forums.sufficientvelocity.com'
THREAD = f'https://{SITE}/threads/a-test-fic.12345'
OTHER = f'https://{SITE}/threads/another-fic.67890'
ALERTS_URL = f'https://{SITE}/account/alerts'

# Timestamps in fixtures/fanfic/alerts.html: thread 12345 is alerted at
# 1600400000 and 1600500000 (plus one alert without a time), thread 67890
# at 1600450000; one quote alert links only to /posts/555/.


class FakeResp:
    def __init__(self, text, url):
        self.text = text
        self.url = url


def _thread_pages(thread: str) -> dict[str, str]:
    return {
        f'{thread}/': (FIXTURES / 'thread_page.html').read_text(),
        f'{thread}/threadmarks': (FIXTURES / 'threadmarks_index.html').read_text(),
        f'{thread}/reader?threadmark_category=1': (FIXTURES / 'reader_p1.html').read_text(),
        f'{thread}/reader/page-2?threadmark_category=1': (FIXTURES / 'reader_p2.html').read_text(),
        f'{thread}/reader?threadmark_category=2': (FIXTURES / 'reader_side_p1.html').read_text(),
    }


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    """Sync import + drain, zero delay, fixture-backed fetches with a log."""
    from backend.routes import fanfic as fanfic_routes

    monkeypatch.setenv('FANFIC_ROOT', str(tmp_path / 'fanfic'))
    monkeypatch.setattr(download, 'REQUEST_DELAY', 0)
    monkeypatch.setattr(fanfic_routes, '_start_import_bg', download.run_import)
    monkeypatch.setattr(fanfic_routes, '_start_drain_bg', download.run_drain_pending)

    pages = {**_thread_pages(THREAD), ALERTS_URL: (FIXTURES / 'alerts.html').read_text()}
    binaries = {'https://example.com/art.png': (b'\x89PNG-fake-bytes', 'image/png')}
    log: list[str] = []

    def fetch(url):
        log.append(url)
        if url not in pages:
            raise RuntimeError(f'404 for {url}')
        return FakeResp(pages[url], url)

    def fetch_binary(url):
        if url not in binaries:
            raise RuntimeError(f'404 for {url}')
        return binaries[url]

    monkeypatch.setattr(download, '_fetch', fetch)
    monkeypatch.setattr(download, '_fetch_binary', fetch_binary)
    return {'pages': pages, 'binaries': binaries, 'log': log}


def _put_cookie(client, domain=SITE):
    resp = client.put('/api/fanfic/cookies', json={'domain': domain, 'cookie': 'xf_user=u1'})
    assert resp.status_code == 200


def _import_fic(client, thread=THREAD) -> str:
    resp = client.post('/api/fanfic/import', json={'url': f'{thread}/'})
    assert resp.status_code == 202, resp.get_json()
    return resp.get_json()['id']


def _set_last_checked(fic_id: str, ts: int) -> None:
    from backend.db.connection import get_db
    db = get_db()
    db.execute('UPDATE fics SET last_checked_at=? WHERE id=?', (ts, fic_id))
    db.commit()


def test_refresh_requires_cookies(client, fake_net):
    resp = client.post('/api/fanfic/refresh-alerts')
    assert resp.status_code == 400
    assert 'cookie' in resp.get_json()['error'].lower()


def test_refresh_updates_stale_and_imports_unknown(client, fake_net):
    fic_id = _import_fic(client)
    _set_last_checked(fic_id, 1000)  # long before every alert
    fake_net['pages'].update(_thread_pages(OTHER))
    _put_cookie(client)

    resp = client.post('/api/fanfic/refresh-alerts')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['flagged'] == 1
    assert body['newImports'] == 1
    assert body['skippedFresh'] == 0
    assert body['errors'] == {}
    assert body['alertsSeen'] == 4  # the /posts/-only quote alert is dropped

    fics = client.get('/api/fanfic').get_json()
    assert len(fics) == 2
    assert all(f['downloadStatus'] == 'complete' for f in fics)
    assert all(f['updatePending'] is False for f in fics)
    imported = next(f for f in fics if f['sourceUrl'] == f'{OTHER}/')
    assert imported['chapterCount'] == 4
    assert imported['title'] == 'A Test Fic'
    stale = next(f for f in fics if f['id'] == fic_id)
    assert stale['lastCheckedAt'] is not None


def test_refresh_skips_fresh_fic(client, fake_net):
    fic_id = _import_fic(client)
    _set_last_checked(fic_id, 1700000000)  # newer than every alert
    fake_net['pages'].update(_thread_pages(OTHER))
    _put_cookie(client)

    before = len(fake_net['log'])
    body = client.post('/api/fanfic/refresh-alerts').get_json()
    assert body['skippedFresh'] == 1
    assert body['flagged'] == 0
    assert body['newImports'] == 1
    # nothing was fetched for the fresh fic — only the alerts page + import
    fetched = fake_net['log'][before:]
    assert not any('a-test-fic.12345' in url for url in fetched)


def test_refresh_dedupes_newest_alert_wins(client, fake_net):
    """Thread 12345 is alerted twice; freshness compares against the newest
    (1600500000), so a fetch between the two timestamps is still stale."""
    fic_id = _import_fic(client)
    _set_last_checked(fic_id, 1600450000)
    fake_net['pages'].update(_thread_pages(OTHER))
    _put_cookie(client)

    body = client.post('/api/fanfic/refresh-alerts').get_json()
    assert body['flagged'] == 1
    assert body['skippedFresh'] == 0


def test_refresh_skips_already_queued_fic(client, fake_net, monkeypatch):
    from backend.routes import fanfic as fanfic_routes
    monkeypatch.setattr(fanfic_routes, '_start_drain_bg', lambda: None)
    fic_id = _import_fic(client)
    _set_last_checked(fic_id, 1000)
    fake_net['pages'].update(_thread_pages(OTHER))
    _put_cookie(client)

    first = client.post('/api/fanfic/refresh-alerts').get_json()
    assert first['flagged'] == 1 and first['newImports'] == 1
    # drain never ran: both the flagged fic and the pending placeholder are
    # already queued, so a second refresh flags nothing new
    body = client.post('/api/fanfic/refresh-alerts').get_json()
    assert body['flagged'] == 0
    assert body['newImports'] == 0
    assert body['skippedActive'] == 2


def test_refresh_still_drains_when_nothing_newly_flagged(client, fake_net):
    """A restart can leave fics stuck at update_pending=1 with no worker
    running to drain them. Refresh must still resume that queue even when
    every thread mentioned in this batch of alerts is already known and
    already queued (flagged == new_imports == 0) — it must not gate the
    drain trigger on having found something new."""
    fic_a = _import_fic(client)
    fake_net['pages'].update(_thread_pages(OTHER))
    fic_b = _import_fic(client, OTHER)
    _put_cookie(client)

    from backend.db.connection import get_db
    db = get_db()
    db.execute('UPDATE fics SET update_pending=1 WHERE id IN (?, ?)', (fic_a, fic_b))
    db.commit()

    body = client.post('/api/fanfic/refresh-alerts').get_json()
    assert body['flagged'] == 0
    assert body['newImports'] == 0
    assert body['skippedActive'] == 2

    fics = {f['id']: f for f in client.get('/api/fanfic').get_json()}
    assert fics[fic_a]['updatePending'] is False
    assert fics[fic_b]['updatePending'] is False


def test_refresh_reports_per_site_errors(client, fake_net):
    fic_id = _import_fic(client)
    _set_last_checked(fic_id, 1000)
    fake_net['pages'].update(_thread_pages(OTHER))
    _put_cookie(client, SITE)
    _put_cookie(client, SV)  # no SV alerts page registered → fetch fails

    body = client.post('/api/fanfic/refresh-alerts').get_json()
    assert SV in body['errors']
    assert body['flagged'] == 1  # the healthy site was still processed


def test_refresh_detects_login_redirect(client, fake_net, monkeypatch):
    _put_cookie(client)
    fixture_fetch = download._fetch

    def fetch(url):
        if url == ALERTS_URL:
            return FakeResp('<html>please log in</html>', f'https://{SITE}/login/')
        return fixture_fetch(url)

    monkeypatch.setattr(download, '_fetch', fetch)
    body = client.post('/api/fanfic/refresh-alerts').get_json()
    assert 'not logged in' in body['errors'][SITE]


def test_check_updates_toggles_queue_flag(client, fake_net, monkeypatch):
    from backend.routes import fanfic as fanfic_routes
    fic_id = _import_fic(client)
    # freeze the drain so the flag is observable
    monkeypatch.setattr(fanfic_routes, '_start_drain_bg', lambda: None)

    resp = client.post(f'/api/fanfic/{fic_id}/check-updates')
    assert resp.status_code == 202
    assert resp.get_json() == {'id': fic_id, 'queued': True}
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['updatePending'] is True

    # clicking again un-queues instead of double-flagging
    resp = client.post(f'/api/fanfic/{fic_id}/check-updates')
    assert resp.status_code == 200
    assert resp.get_json() == {'id': fic_id, 'queued': False}
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['updatePending'] is False


def test_check_updates_conflicts_while_downloading(client, fake_net):
    from backend.db.connection import get_db
    fic_id = _import_fic(client)
    db = get_db()
    db.execute("UPDATE fics SET download_status='downloading' WHERE id=?", (fic_id,))
    db.commit()
    assert client.post(f'/api/fanfic/{fic_id}/check-updates').status_code == 409


def test_drain_runs_strictly_serially(client, fake_net):
    """Two queued fics are drained one at a time in updated_at order — every
    fetch for the first finishes before the second's begin."""
    from backend.db.connection import get_db
    fic_a = _import_fic(client)
    fake_net['pages'].update(_thread_pages(OTHER))
    fic_b = _import_fic(client, OTHER)

    db = get_db()
    db.execute('UPDATE fics SET update_pending=1, updated_at=1 WHERE id=?', (fic_a,))
    db.execute('UPDATE fics SET update_pending=1, updated_at=2 WHERE id=?', (fic_b,))
    db.commit()

    start = len(fake_net['log'])
    download.run_drain_pending()
    fetched = fake_net['log'][start:]
    a_idx = [i for i, url in enumerate(fetched) if '.12345' in url]
    b_idx = [i for i, url in enumerate(fetched) if '.67890' in url]
    assert a_idx and b_idx
    assert max(a_idx) < min(b_idx)


def test_drain_survives_one_fic_failing(client, fake_net):
    from backend.db.connection import get_db
    fic_a = _import_fic(client)
    fake_net['pages'].update(_thread_pages(OTHER))
    fic_b = _import_fic(client, OTHER)
    # fic A's threadmarks index vanishes → its update fails
    del fake_net['pages'][f'{THREAD}/threadmarks']

    db = get_db()
    db.execute('UPDATE fics SET update_pending=1, updated_at=1 WHERE id=?', (fic_a,))
    db.execute('UPDATE fics SET update_pending=1, updated_at=2 WHERE id=?', (fic_b,))
    db.commit()

    download.run_drain_pending()
    a = client.get(f'/api/fanfic/{fic_a}').get_json()
    b = client.get(f'/api/fanfic/{fic_b}').get_json()
    assert a['downloadStatus'] == 'error'
    assert a['updatePending'] is False
    assert b['downloadStatus'] == 'complete'
    assert b['updatePending'] is False
