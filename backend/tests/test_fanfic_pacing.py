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
    assert sleeps == [15]
    assert pacing.state()['next_request'] == 1030
    pacing.before_request('https://archiveofourown.org/works/1')
    assert sleeps == [15]


def test_retry_after_formats():
    now = 1700000000
    assert pacing.retry_delay('120', now) == 120
    assert pacing.retry_delay(format_datetime(datetime.fromtimestamp(now + 3600, timezone.utc)), now) == 3600
    assert pacing.retry_delay('broken', now) is None
    assert pacing.retry_delay('-1', now) is None


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
