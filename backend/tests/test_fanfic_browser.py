"""Browser handoff tests use real isolated SQLite and no external requests."""
import time

import pytest

from backend.db import connection
from backend.db.connection import get_db
from backend.fanfic import browser, collections, download, pacing, sites

CLIENT = 'test-browser-client-1234'
OTHER = 'other-browser-client-1234'
URL = 'https://www.fanfiction.net/s/123/1/'


def story(chapter=1):
    return f'''<div id="profile_top"><b>A story</b><a href="/u/42/Writer">Writer</a></div>
    <select name="chapter"><option value="1">First</option><option value="2">Second</option></select>
    <div id="storytext"><p>Chapter {chapter} text</p></div>'''


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(browser.time, 'time', lambda: now[0])
    monkeypatch.setattr(collections, 'start_scans', lambda: None)
    monkeypatch.setattr(download, 'start_drain', lambda: None)
    monkeypatch.setattr(download, '_http_get', lambda *a, **k: pytest.fail('Unexpected HTTP fetch'))
    browser.set_mode('browser')
    browser.poll(CLIENT)
    return now


def queue():
    fic_id, _ = collections.queue_work(sites.parse_work_url(URL))
    download.run_drain_pending()
    return fic_id


def answer(job, chapter=1):
    submit(job, {'kind': 'page', 'url': job['url'], 'html': story(chapter)})


def submit(job, payload, client=CLIENT):
    browser.submit(job['id'], client, {**payload, 'attemptId': job['attemptId']})


def test_browser_import_preserves_pages_across_disconnect_and_restart(clock):
    fic_id = queue()
    first = browser.poll(CLIENT)['request']
    assert first['navigate'] and first['url'] == URL
    answer(first)
    # Simulate a server restart after receiving a page, before parsing it.
    connection._conn.close()
    connection._conn = None
    download.run_drain_pending()
    chapter = get_db().execute('SELECT id FROM fic_chapters WHERE fic_id=?', (fic_id,)).fetchone()[0]
    assert browser.poll(CLIENT)['request'] is None  # Ten-minute interval.
    browser.disconnect(CLIENT)
    assert not browser.status()['connected']
    assert get_db().execute('SELECT update_pending FROM fics WHERE id=?', (fic_id,)).fetchone()[0] == 1
    clock[0] += 600
    second = browser.poll(CLIENT)['request']
    assert second['url'].endswith('/2/')
    answer(second, 2)
    answer(second, 2)  # Lost acknowledgement is harmless.
    download.run_drain_pending()
    assert get_db().execute('SELECT chapter_count,download_status FROM fics').fetchone()[:] == (2, 'complete')
    assert get_db().execute('SELECT id FROM fic_chapters WHERE position=1').fetchone()[0] == chapter
    assert not get_db().execute('SELECT 1 FROM fanfic_browser_requests').fetchone()


@pytest.mark.parametrize('kind', ['challenge', 'login', 'error'])
def test_attention_waits_for_manual_continue_and_never_stores_wall(clock, kind):
    queue()
    job = browser.poll(CLIENT)['request']
    submit(job, {'kind': kind})
    assert browser.status()['needsAttention']
    clock[0] += 86400
    blocked = browser.poll(OTHER)['request']
    assert not blocked['navigate'] and blocked['needsAttention']
    assert not get_db().execute('SELECT html FROM fanfic_browser_requests').fetchone()[0]
    with pytest.raises(browser.BrowserConflict):
        answer(job)
    submit(job, {'kind': 'page', 'url': URL, 'html': story()}, client=OTHER)
    assert not browser.status()['needsAttention']


def test_pause_retry_and_rate_limit_never_bypass_deadline(clock):
    queue()
    pacing.pause()
    assert browser.poll(CLIENT)['request'] is None
    pacing.resume()
    job = browser.poll(CLIENT)['request']
    submit(job, {'kind': 'rate_limit', 'retryAfter': '7200'})
    pacing.pause()
    pacing.resume()
    assert browser.poll(CLIENT)['request'] is None
    clock[0] += 7200
    job = browser.poll(CLIENT)['request']
    submit(job, {'kind': 'challenge'})
    browser.retry(job['id'], CLIENT)
    assert browser.poll(CLIENT)['request'] is None
    clock[0] += 600
    assert browser.poll(CLIENT)['request']['navigate']


def test_only_one_browser_can_claim_and_old_replies_are_rejected(clock):
    queue()
    job = browser.poll(CLIENT)['request']
    with pytest.raises(browser.BrowserConflict):
        browser.poll(OTHER)
    clock[0] += 600
    assert browser.poll(OTHER)['request']['navigate']
    with pytest.raises(browser.BrowserConflict):
        answer(job)


@pytest.mark.parametrize('url', ['http://www.fanfiction.net/s/123/1/', 'https://evil.test/s/123/1/',
                               'https://www.fanfiction.net/s/123/2/', 'https://www.fanfiction.net/s/999/1/',
                               'https://user@www.fanfiction.net/s/123/1/', 'https://www.fanfiction.net:444/s/123/1/'])
def test_rejects_wrong_page_or_origin(clock, url):
    queue()
    job = browser.poll(CLIENT)['request']
    with pytest.raises(ValueError):
        submit(job, {'kind': 'page', 'url': url, 'html': story()})
    assert not get_db().execute('SELECT html FROM fanfic_browser_requests').fetchone()[0]


def test_missing_content_is_blocked_not_imported(clock):
    queue()
    job = browser.poll(CLIENT)['request']
    submit(job, {'kind': 'page', 'url': URL, 'html': '<title>Just a moment</title>'})
    assert browser.status()['needsAttention']
    assert not get_db().execute('SELECT 1 FROM fic_chapters').fetchone()


def test_browser_collection_without_copied_cookies_checkpoints_page(client, clock):
    result = client.post('/api/fanfic/collections', json={'site': 'fanfiction.net', 'collection': 'favorites'})
    assert result.status_code == 202
    scan_id = result.json['id']
    collections.run_scan(scan_id)
    job = browser.poll(CLIENT)['request']
    submit(job, {'kind': 'page', 'url': job['url'],
                   'html': f'<table id="gui_table1"><tr><td><a href="{URL}">Story</a></td></tr></table>'})
    collections.run_scan(scan_id)
    assert get_db().execute('SELECT status,pages,found FROM fanfic_collection_scans').fetchone()[:] == ('complete', 1, 1)
    assert get_db().execute('SELECT update_pending FROM fics').fetchone()[0] == 1


def test_other_sites_run_while_browser_waits(clock, monkeypatch):
    queue()
    fic_id, _ = collections.queue_work(sites.parse_work_url('https://archiveofourown.org/works/12'))
    monkeypatch.setattr(collections, '_fetch', lambda url: type('Response', (), {'text':
        '<div id="workskin"><div class="preface"><h2 class="title">Other</h2></div>'
        '<div id="chapters"><div class="userstuff">Text</div></div></div>'})())
    download.run_drain_pending()
    assert get_db().execute('SELECT download_status FROM fics WHERE id=?', (fic_id,)).fetchone()[0] == 'complete'


def test_routes_validate_body_owner_and_mode(client, clock):
    assert client.put('/api/fanfic/browser', json={'mode': 'bogus'}).status_code == 400
    assert client.post('/api/fanfic/browser/poll', json=[]).status_code == 400
    assert client.post('/api/fanfic/browser/poll', json={'clientId': 'x'}).status_code == 400
    assert client.post('/api/fanfic/browser/poll', json={'clientId': OTHER}).status_code == 409
    queue()
    job = browser.poll(CLIENT)['request']
    assert client.post(f"/api/fanfic/browser/{job['id']}/result", json={'clientId': OTHER, 'kind': 'page'}).status_code == 409
    assert client.get('/api/fanfic/site-limit').json['browser']['connected']
    browser.set_mode('http')
    with pytest.raises(browser.BrowserConflict):
        answer(job)


def test_cancel_removes_outstanding_page(client, clock):
    fic_id = queue()
    assert client.post(f'/api/fanfic/{fic_id}/check-updates').json['queued'] is False
    assert browser.poll(CLIENT)['request'] is None


def test_retry_rejects_delayed_reply_from_previous_attempt(clock):
    queue()
    old = browser.poll(CLIENT)['request']
    submit(old, {'kind': 'error'})
    browser.retry(old['id'], CLIENT)
    clock[0] += 600
    new = browser.poll(CLIENT)['request']
    assert new['id'] == old['id'] and new['attemptId'] != old['attemptId']
    with pytest.raises(browser.BrowserConflict):
        answer(old)
    answer(new)


def test_deep_intent_survives_restart_of_active_worker(clock):
    fic_id = queue()
    db = get_db()
    db.execute("UPDATE fics SET deep_pending=1,download_status='downloading',update_pending=0 WHERE id=?", (fic_id,))
    db.commit()
    connection._reset_stale_fic_downloads(db)
    assert db.execute('SELECT deep_pending,update_pending FROM fics WHERE id=?', (fic_id,)).fetchone()[:] == (1, 1)


def test_browser_mode_without_connection_does_not_use_http(clock):
    browser.disconnect(CLIENT)
    fic_id = queue()
    assert get_db().execute('SELECT update_pending FROM fics WHERE id=?', (fic_id,)).fetchone()[0] == 1
    assert not get_db().execute('SELECT 1 FROM fanfic_browser_requests').fetchone()
