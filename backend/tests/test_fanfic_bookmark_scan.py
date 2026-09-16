"""Bookmark-label scan: the same walk as the watched-threads scan, plus the
folders. Network is monkeypatched to inline HTML and the scan runs
synchronously, mirroring test_fanfic_watched_scan.py's setup."""

import pytest

from backend.db.connection import get_db
from backend.fanfic import download

SITE = 'forums.spacebattles.com'
P1 = f'https://{SITE}/account/bookmarks'
P2 = f'https://{SITE}/account/bookmarks?page=2'


def row(thread_id, slug, labels, pages=2):
    pills = ''.join(
        f'<a href="/account/bookmarks?label={l.replace(" ", "+")}">{l}</a>' for l in labels)
    return f'''
    <div class="structItem structItem--bookmark">
      <div class="structItem-title"><a href="/threads/{slug}.{thread_id}/">{slug}</a></div>
      <div class="structItem-minor">{pills}</div>
    </div>
    <div class="pageNav-main"><a class="pageNav-page">1</a>
      <a class="pageNav-page">{pages}</a></div>'''


class FakeResp:
    def __init__(self, text, url):
        self.text = text
        self.url = url


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    from backend.routes import fanfic as fanfic_routes

    monkeypatch.setenv('FANFIC_ROOT', str(tmp_path / 'fanfic'))
    monkeypatch.setattr(download, 'REQUEST_DELAY', 0)
    monkeypatch.setattr(fanfic_routes, '_start_bookmark_scan_bg', download.run_bookmark_scan)

    pages = {
        P1: row('11111', 'a-worm-fic', ['Slow Burn', 'Worm']),
        P2: row('22222', 'another-fic', ['Worm']),
    }
    log: list[str] = []

    def fetch(url):
        log.append(url)
        if url not in pages:
            # The drain worker follows each queued fic; no thread fixtures
            # here, so those downloads fail and leave the rows in place.
            raise RuntimeError(f'404 for {url}')
        return FakeResp(pages[url], url)

    monkeypatch.setattr(download, '_fetch', fetch)
    return {'pages': pages, 'log': log}


def _put_cookie(client, domain=SITE):
    assert client.put('/api/fanfic/cookies',
                      json={'domain': domain, 'cookie': 'xf_user=u1'}).status_code == 200


def _scan_state(client, domain=SITE):
    cookies = client.get('/api/fanfic/cookies').get_json()
    return next(c for c in cookies if c['domain'] == domain).get('bookmarkScan')


def _folders(client):
    return {f['name']: f['ficCount'] for f in client.get('/api/fanfic/folders').get_json()}


def test_scan_requires_cookie(client, fake_net):
    resp = client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    assert resp.status_code == 400
    assert 'cookie' in resp.get_json()['error'].lower()


def test_scan_rejects_unknown_domain(client, fake_net):
    _put_cookie(client)
    assert client.post('/api/fanfic/scan-bookmarks/example.com').status_code == 400


def test_scan_imports_bookmarks_and_files_them_by_label(client, fake_net):
    _put_cookie(client)
    assert client.post(f'/api/fanfic/scan-bookmarks/{SITE}').status_code == 202

    fics = client.get('/api/fanfic').get_json()
    assert {f['sourceUrl'].split('.')[-1].strip('/') for f in fics} == {'11111', '22222'}
    assert _folders(client) == {'Slow Burn': 1, 'Worm': 2}

    state = _scan_state(client)
    assert (state['found'], state['imported'], state['alreadyInLibrary']) == (2, 2, 0)
    assert state['foldered'] == 2
    assert state['done'] is True and state['error'] is None
    assert state['page'] == 1  # wrapped, so a later click rescans fresh


def test_scan_files_a_fic_the_library_already_has(client, fake_net):
    assert client.post('/api/fanfic/import', json={
        'url': f'https://{SITE}/threads/a-worm-fic.11111/'}).status_code in (200, 202)
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')

    state = _scan_state(client)
    assert (state['found'], state['alreadyInLibrary'], state['imported']) == (2, 1, 1)
    assert _folders(client) == {'Slow Burn': 1, 'Worm': 2}


def test_a_label_removed_on_the_site_unfiles_the_fic_on_the_next_scan(client, fake_net):
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    fake_net['pages'][P1] = row('11111', 'a-worm-fic', ['Worm'])
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    assert _folders(client) == {'Slow Burn': 0, 'Worm': 2}


def test_filing_by_hand_survives_the_next_scan(client, fake_net):
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    fic_id = next(f['id'] for f in client.get('/api/fanfic').get_json()
                  if f['sourceUrl'].endswith('11111/'))
    folder_id = client.post('/api/fanfic/folders',
                            json={'name': 'Currently reading'}).get_json()['id']
    assert client.post(f'/api/fanfic/{fic_id}/folders',
                       json={'folderId': folder_id}).status_code == 200

    fake_net['pages'][P1] = row('11111', 'a-worm-fic', [])
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    assert _folders(client)['Currently reading'] == 1


def test_hand_filing_claims_a_membership_the_sync_made(client, fake_net):
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    fic_id = next(f['id'] for f in client.get('/api/fanfic').get_json()
                  if f['sourceUrl'].endswith('11111/'))
    folder_id = next(f['id'] for f in client.get('/api/fanfic/folders').get_json()
                     if f['name'] == 'Slow Burn')
    # Re-adding by hand upgrades the row, so dropping the label no longer
    # takes the fic out of the folder.
    client.post(f'/api/fanfic/{fic_id}/folders', json={'folderId': folder_id})
    fake_net['pages'][P1] = row('11111', 'a-worm-fic', ['Worm'])
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    assert _folders(client)['Slow Burn'] == 1


def test_an_imported_folder_is_marked_as_one(client, fake_net):
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    origins = {f['name']: f['origin']
               for f in client.get('/api/fanfic/folders').get_json()}
    assert origins == {'Slow Burn': 'import', 'Worm': 'import'}
    assert client.post('/api/fanfic/folders', json={'name': 'By hand'}).status_code == 201
    assert {f['name']: f['origin'] for f in
            client.get('/api/fanfic/folders').get_json()}['By hand'] == 'manual'


def test_scan_conflicts_while_active(client, fake_net, monkeypatch):
    from backend.routes import fanfic as fanfic_routes
    monkeypatch.setattr(fanfic_routes, '_start_bookmark_scan_bg', lambda domain: None)
    _put_cookie(client)
    with download._bookmark_scan_lock:
        download._bookmark_scan_progress[SITE] = {
            'page': 1, 'lastPage': None, 'found': 0, 'imported': 0,
            'alreadyInLibrary': 0, 'foldered': 0, 'done': False, 'error': None,
        }
    try:
        assert client.post(f'/api/fanfic/scan-bookmarks/{SITE}').status_code == 409
    finally:
        download._bookmark_scan_progress.pop(SITE, None)


def test_scan_resumes_from_its_checkpoint(client, fake_net, monkeypatch):
    _put_cookie(client)
    real_fetch = download._fetch

    def flaky(url):
        if url == P2:
            raise RuntimeError('simulated crash before page 2')
        return real_fetch(url)

    monkeypatch.setattr(download, '_fetch', flaky)
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    row_ = get_db().execute(
        'SELECT next_page, found FROM fanfic_bookmark_scans WHERE domain=?',
        (SITE,)).fetchone()
    assert (row_['next_page'], row_['found']) == (2, 1)
    assert _scan_state(client)['error'] is not None

    monkeypatch.setattr(download, '_fetch', real_fetch)
    fake_net['log'].clear()
    client.post(f'/api/fanfic/scan-bookmarks/{SITE}')
    assert P1 not in fake_net['log'] and P2 in fake_net['log']
    assert _scan_state(client)['error'] is None
    assert _folders(client) == {'Slow Burn': 1, 'Worm': 2}
