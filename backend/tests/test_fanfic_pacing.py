from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

from backend.db.connection import get_db
from backend.fanfic import collections, download, pacing, sites


def response(code=429, headers=None, text=''):
    return SimpleNamespace(status_code=code, headers=headers or {}, text=text, close=lambda: None)


def test_pacing_reserves_slot_and_waits(monkeypatch):
    clock = [1000.0]
    sleeps = []
    monkeypatch.setattr(pacing.time, 'time', lambda: clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(pacing.time, 'sleep', sleep)
    pacing.before_request('https://www.fanfiction.net/s/1/1/')
    pacing.before_request('https://m.fanfiction.net/s/2/1/')
    assert sum(sleeps) == 600
    assert max(sleeps) <= 1
    assert pacing.state()['next_request'] == 2200
    pacing.before_request('https://archiveofourown.org/works/1')
    assert sum(sleeps) == 600


def test_manual_pause_persists_and_resume_preserves_spacing(client, monkeypatch):
    from backend.db import connection
    monkeypatch.setattr(pacing.time, 'time', lambda: 1000)
    monkeypatch.setattr(collections, 'start_scans', lambda: None)
    monkeypatch.setattr(download, 'start_drain', lambda: None)
    pacing.before_request('https://www.fanfiction.net/s/1/1/')
    assert client.post('/api/fanfic/site-limit/pause').status_code == 200
    connection._conn.close()
    connection._conn = None
    status = client.get('/api/fanfic/site-limit').json
    assert status['paused']
    assert status['reason'] == 'FF.net downloads paused by you.'
    assert status['nextRequest'] == 1600
    with pytest.raises(pacing.DeferredDownload):
        pacing.before_request('https://www.fanfiction.net/s/1/2/')
    client.post('/api/fanfic/site-limit/resume')
    assert not pacing.state()['paused']
    assert pacing.state()['next_request'] == 1600


def test_pause_during_interval_releases_fetch_lock_and_stops_request(monkeypatch):
    monkeypatch.setattr(pacing.time, 'time', lambda: 1000)
    pacing.before_request('https://www.fanfiction.net/s/1/1/')
    def wait(seconds):
        assert seconds <= 1
        assert download._fetch_lock.acquire(blocking=False)
        download._fetch_lock.release()
        pacing.pause()
    monkeypatch.setattr(pacing.time, 'sleep', wait)
    monkeypatch.setattr(download, '_http_get', lambda *a, **kw: pytest.fail('Paused request sent'))
    with pytest.raises(pacing.DeferredDownload):
        download._fetch('https://www.fanfiction.net/s/1/2/', same_host=True)


def test_rate_limit_response_cannot_clear_manual_pause():
    pacing.pause()
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response())
    assert pacing.state()['paused']
    assert pacing.state()['reason'] == 'FF.net downloads paused by you.'


def test_retry_after_formats():
    now = 1700000000
    assert pacing.retry_delay('120', now) == 120
    assert pacing.retry_delay(format_datetime(datetime.fromtimestamp(now + 3600, timezone.utc)), now) == 3600
    assert pacing.retry_delay('broken', now) is None
    assert pacing.retry_delay('-1', now) is None


def test_interval_api_persists_and_adjusts_wait_without_clearing_limits(client, monkeypatch):
    from backend.db import connection
    monkeypatch.setattr(pacing.time, 'time', lambda: 1000)
    pacing.before_request('https://www.fanfiction.net/s/1/1/')
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response(headers={'Retry-After': '7200'}))
    pacing.pause()
    result = client.put('/api/fanfic/site-limit', json={'interval': 1200})
    assert result.status_code == 200
    assert pacing.state()['next_request'] == 2200
    assert result.json['paused']
    assert result.json['cooldownUntil'] == 8200
    connection._conn.close()
    connection._conn = None
    assert client.get('/api/fanfic/site-limit').json['interval'] == 1200
    client.put('/api/fanfic/site-limit', json={'interval': 60})
    assert pacing.state()['next_request'] == 1060
    assert pacing.state()['cooldown_until'] == 8200


@pytest.mark.parametrize('value', [None, True, '600', 14, 86401, 60.5])
def test_invalid_interval_rejected(client, value):
    assert client.put('/api/fanfic/site-limit', json={'interval': value}).status_code == 400
    assert pacing.state()['request_interval'] == 600


def test_configured_interval_controls_requests(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pacing.time, 'time', lambda: clock[0])
    monkeypatch.setattr(pacing.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    pacing.set_interval(120)
    pacing.before_request('https://www.fanfiction.net/s/1/1/')
    pacing.before_request('https://www.fanfiction.net/s/1/2/')
    assert clock[0] == 1120
    assert pacing.state()['next_request'] == 1240


def test_interval_migration_is_idempotent_and_preserves_existing_pause():
    import sqlite3
    from backend.db.connection import _ensure_fanfic_request_interval
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE fanfic_site_limits(domain TEXT PRIMARY KEY, paused INTEGER)')
        db.execute("INSERT INTO fanfic_site_limits VALUES ('fanfiction.net',1)")
        _ensure_fanfic_request_interval(db)
        db.execute('UPDATE fanfic_site_limits SET request_interval=1200')
        _ensure_fanfic_request_interval(db)
        assert db.execute('SELECT paused,request_interval FROM fanfic_site_limits').fetchone() == (1, 1200)


def test_cooldown_persists_and_manual_resume_cannot_shorten_it(monkeypatch):
    monkeypatch.setattr(pacing.time, 'time', lambda: 1000)
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response(headers={'Retry-After': '3600'}))
    assert pacing.state()['cooldown_until'] == 4600
    pacing.resume()
    assert not pacing.ready()
    assert pacing.state()['cooldown_until'] == 4600
    with pytest.raises(pacing.DeferredDownload):
        pacing.before_request('https://www.fanfiction.net/s/1/1/')
    monkeypatch.setattr(pacing.time, 'time', lambda: 4601)
    assert pacing.ready()


def test_missing_retry_after_exponentially_backs_off(monkeypatch):
    monkeypatch.setattr(pacing.time, 'time', lambda: 1000)
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response())
    assert pacing.state()['cooldown_until'] == 1900
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response())
    assert pacing.state()['cooldown_until'] == 2800


def test_cloudflare_pause_has_no_automatic_expiry(monkeypatch):
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response(403), challenge=True)
    monkeypatch.setattr(pacing.time, 'time', lambda: 9999999999)
    assert not pacing.ready()
    pacing.resume()
    assert pacing.ready()


@pytest.mark.parametrize('code,text', [(429, ''), (403, 'Just a moment')])
def test_fetch_stops_after_first_site_block(monkeypatch, code, text):
    calls = []
    def http(url, **kwargs):
        calls.append(url)
        return response(code, text=text)
    monkeypatch.setattr(download, '_http_get', http)
    with pytest.raises(pacing.DeferredDownload):
        download._fetch('https://www.fanfiction.net/s/1/1/', same_host=True)
    with pytest.raises(pacing.DeferredDownload):
        download._fetch('https://www.fanfiction.net/s/2/1/', same_host=True)
    assert len(calls) == 1


def test_queue_retains_blocked_work_and_runs_other_sites(monkeypatch):
    ids = [collections.queue_work(sites.parse_work_url(url))[0] for url in (
        'https://www.fanfiction.net/s/1/1/', 'https://www.fanfiction.net/s/2/1/',
        'https://archiveofourown.org/works/3')]
    db = get_db()
    for order, fic_id in enumerate(ids):
        db.execute('UPDATE fics SET updated_at=? WHERE id=?', (order, fic_id))
    db.execute('UPDATE fics SET deep_pending=1 WHERE id=?', (ids[0],))
    db.commit()
    calls = []
    def fetch(url):
        calls.append(url)
        if pacing.applies(url):
            pacing.suspend(response(403), challenge=True)
        return SimpleNamespace(text='''<div id="workskin"><div class="preface"><h2 class="title">Story</h2></div>
          <div id="chapters"><div class="userstuff">Saved prose</div></div></div>''')
    monkeypatch.setattr(collections, '_fetch', fetch)
    download.run_drain_pending()
    assert len(calls) == 2
    assert db.execute('SELECT update_pending,deep_pending FROM fics WHERE id=?', (ids[0],)).fetchone()[:] == (1, 1)
    assert db.execute('SELECT update_pending,download_error FROM fics WHERE id=?', (ids[1],)).fetchone()[:] == (1, None)
    assert db.execute('SELECT chapter_count FROM fics WHERE id=?', (ids[2],)).fetchone()[0] == 1


def test_collection_pause_keeps_checkpoint(monkeypatch):
    scan_id = collections.create_scan('fanfiction.net', 'all', '')
    before = get_db().execute('SELECT remaining_urls FROM fanfic_collection_scans').fetchone()[0]
    def fetch(url):
        pacing.suspend(response())
    monkeypatch.setattr(collections, '_fetch', fetch)
    collections.run_scan(scan_id)
    row = get_db().execute('SELECT status,remaining_urls FROM fanfic_collection_scans').fetchone()
    assert row[:] == ('pending', before)


def test_resume_api_preserves_server_deadline(client, monkeypatch):
    monkeypatch.setattr(collections, 'start_scans', lambda: None)
    monkeypatch.setattr(download, 'start_drain', lambda: None)
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response(headers={'Retry-After': '7200'}))
    with pytest.raises(pacing.DeferredDownload):
        pacing.suspend(response(403), challenge=True)
    before = client.get('/api/fanfic/site-limit').json
    assert before['paused']
    assert client.post('/api/fanfic/site-limit/resume').status_code == 200
    after = client.get('/api/fanfic/site-limit').json
    assert not after['paused']
    assert after['cooldownUntil'] == before['cooldownUntil']


def test_scheduler_respects_test_boundary(monkeypatch):
    monkeypatch.setenv('LUNASCHAL_NO_SCHEDULERS', '1')
    monkeypatch.setattr(pacing.threading, 'Thread', lambda **kwargs: pytest.fail('Unexpected thread'))
    pacing.start_scheduler()
