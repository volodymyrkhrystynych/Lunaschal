"""Torrent route behaviour. No real qBittorrent and no real tunnel — every call
into the client and the VPN control server is monkeypatched on the module the
routes imported.
"""
import base64

import pytest

from backend.db.connection import get_db
from backend.torrent import client as torrent_client
from backend.torrent import vpn as torrent_vpn

HEX = 'c9e15763f722f23e98a29decdfae341b98d53056'
HEX2 = 'a' * 40
MAGNET = f'magnet:?xt=urn:btih:{HEX}&dn=Debian+ISO'


def live(hash_=HEX, **over):
    base = {
        'hash': hash_, 'name': 'Debian ISO', 'state': 'downloading', 'progress': 0.5,
        'size': 100, 'downloaded': 50, 'uploaded': 0, 'ratio': 0.0,
        'dlspeed': 10, 'upspeed': 0, 'eta': 120, 'num_seeds': 3, 'num_leechs': 1,
        'category': '', 'save_path': '/downloads', 'content_path': '/downloads/Debian ISO',
        'added_on': 1000, 'completion_on': 0, 'ratio_limit': -2,
        'seeding_time_limit': -2, 'dl_limit': 0, 'up_limit': 0,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def tunnel_up(monkeypatch):
    """Default to a healthy tunnel; the tests that care override it."""
    monkeypatch.setattr(torrent_vpn, 'status', lambda **kw: {
        'available': True, 'connected': True, 'status': 'running',
        'ip': '185.111.110.66', 'country': 'Canada', 'city': 'Toronto',
        'forwardedPort': 51413,
    })


@pytest.fixture
def fake_client(monkeypatch):
    calls = {'added': [], 'files': [], 'deleted': [], 'paused': [], 'limits': []}
    state = {'live': []}

    monkeypatch.setattr(torrent_client, 'torrents_info',
                        lambda hashes=None: state['live'])

    def add_magnets(urls, category=None, paused=False):
        calls['added'].append({'urls': urls, 'category': category, 'paused': paused})
        state['live'].extend(live(hash_=HEX) for _ in urls)

    def add_files(files, category=None, paused=False):
        calls['files'].append({'names': [n for n, _ in files], 'category': category})

    monkeypatch.setattr(torrent_client, 'add_magnets', add_magnets)
    monkeypatch.setattr(torrent_client, 'add_files', add_files)
    monkeypatch.setattr(torrent_client, 'pause', lambda h: calls['paused'].append(h))
    monkeypatch.setattr(torrent_client, 'resume', lambda h: None)
    monkeypatch.setattr(torrent_client, 'recheck', lambda h: None)
    monkeypatch.setattr(torrent_client, 'delete',
                        lambda h, delete_files=False: calls['deleted'].append((h, delete_files)))
    monkeypatch.setattr(torrent_client, 'set_category', lambda h, c: None)
    monkeypatch.setattr(torrent_client, 'set_share_limits',
                        lambda h, **kw: calls['limits'].append(kw))
    monkeypatch.setattr(torrent_client, 'set_download_limit', lambda h, n: None)
    monkeypatch.setattr(torrent_client, 'set_upload_limit', lambda h, n: None)
    monkeypatch.setattr(torrent_client, 'categories', lambda: {'linux': {}, 'audio': {}})
    monkeypatch.setattr(torrent_client, 'create_category', lambda n, save_path='': None)
    monkeypatch.setattr(torrent_client, 'remove_category', lambda n: None)
    calls['state'] = state
    return calls


def down(monkeypatch):
    def boom(*a, **kw):
        raise torrent_client.TorrentClientUnavailable('connection refused')
    for name in ('torrents_info', 'add_magnets', 'add_files', 'pause', 'delete',
                 'categories', 'torrent_files'):
        monkeypatch.setattr(torrent_client, name, boom)


# --- listing ---------------------------------------------------------------


def test_list_returns_torrents_and_the_tunnel_banner_in_one_call(client, fake_client):
    """One round trip per poll: the list refreshes every 1.5s and the banner
    has to move with it."""
    fake_client['state']['live'] = [live()]
    body = client.get('/api/torrents').get_json()
    assert body['torrents'][0]['infoHash'] == HEX
    assert body['vpn']['ip'] == '185.111.110.66'


def test_a_stopped_stack_is_a_503_with_something_to_do_about_it(client, monkeypatch):
    down(monkeypatch)
    resp = client.get('/api/torrents')
    assert resp.status_code == 503
    assert 'systemctl --user start lunaschal-torrent' in resp.get_json()['error']


# --- adding ----------------------------------------------------------------


def test_adding_a_magnet_stores_a_row_keyed_by_infohash(client, fake_client):
    resp = client.post('/api/torrents', json={'magnets': MAGNET, 'note': 'for the server'})
    assert resp.status_code == 202
    assert resp.get_json()['added'] == [{'infoHash': HEX, 'name': 'Debian ISO'}]
    row = get_db().execute('SELECT * FROM torrents WHERE info_hash=?', (HEX,)).fetchone()
    assert row['note'] == 'for the server'
    assert row['source'] == 'magnet'


def test_a_paste_of_several_magnets_adds_them_all_in_one_call(client, fake_client):
    other = f'magnet:?xt=urn:btih:{HEX2}&dn=Second'
    resp = client.post('/api/torrents', json={'magnets': f'{MAGNET}\n\n{other}\n'})
    assert resp.status_code == 202
    assert len(resp.get_json()['added']) == 2
    assert len(fake_client['added'][0]['urls']) == 2


def test_one_bad_line_does_not_discard_the_good_ones(client, fake_client):
    resp = client.post('/api/torrents', json={'magnets': f'{MAGNET}\nnot-a-magnet'})
    body = resp.get_json()
    assert resp.status_code == 202
    assert [a['infoHash'] for a in body['added']] == [HEX]
    assert len(body['errors']) == 1


def test_only_bad_lines_is_a_400(client, fake_client):
    resp = client.post('/api/torrents', json={'magnets': 'nope\nalso nope'})
    assert resp.status_code == 400
    assert len(resp.get_json()['errors']) == 2


def test_a_base32_magnet_lands_on_the_same_row_as_its_hex_twin(client, fake_client):
    """Re-adding the same torrent from a differently-spelled link must not
    create a second row."""
    client.post('/api/torrents', json={'magnets': MAGNET})
    b32 = base64.b32encode(bytes.fromhex(HEX)).decode()
    client.post('/api/torrents', json={'magnets': f'magnet:?xt=urn:btih:{b32}'})
    count = get_db().execute('SELECT COUNT(*) c FROM torrents').fetchone()['c']
    assert count == 1


def test_add_is_refused_while_the_tunnel_is_down(client, fake_client, monkeypatch):
    """The container cannot leak either way — the kill switch is the network
    namespace — but a stalled torrent looks like a dead swarm, so say it."""
    monkeypatch.setattr(torrent_vpn, 'status', lambda **kw: {
        'available': True, 'connected': False, 'status': 'stopped',
        'ip': None, 'country': None, 'city': None, 'forwardedPort': None,
    })
    resp = client.post('/api/torrents', json={'magnets': MAGNET})
    assert resp.status_code == 409
    assert fake_client['added'] == []
    assert get_db().execute('SELECT COUNT(*) c FROM torrents').fetchone()['c'] == 0


def test_the_vpn_gate_can_be_turned_off(client, fake_client, monkeypatch):
    get_db().execute('UPDATE settings SET torrent_require_vpn=0')
    get_db().commit()
    monkeypatch.setattr(torrent_vpn, 'status', lambda **kw: {
        'available': False, 'connected': False, 'status': 'unreachable',
        'ip': None, 'country': None, 'city': None, 'forwardedPort': None,
    })
    assert client.post('/api/torrents', json={'magnets': MAGNET}).status_code == 202


def test_uploading_a_torrent_file_stores_a_row_from_its_infohash(client, fake_client):
    import hashlib
    import io
    info = b'd6:lengthi1024e4:name8:test.iso12:piece lengthi16384e6:pieces0:e'
    blob = b'd4:info' + info + b'e'
    resp = client.post(
        '/api/torrents',
        data={'files': (io.BytesIO(blob), 'test.torrent')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 202
    expected = hashlib.sha1(info).hexdigest()
    row = get_db().execute('SELECT * FROM torrents WHERE info_hash=?', (expected,)).fetchone()
    assert row is not None and row['source'] == 'file'


def test_a_rejected_upload_leaves_no_row(client, fake_client):
    import io
    resp = client.post(
        '/api/torrents',
        data={'files': (io.BytesIO(b'not bencode'), 'bad.torrent')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400
    assert get_db().execute('SELECT COUNT(*) c FROM torrents').fetchone()['c'] == 0


def test_a_client_that_refuses_the_add_leaves_no_row(client, fake_client, monkeypatch):
    def boom(*a, **kw):
        raise torrent_client.TorrentClientError('unsupported')
    monkeypatch.setattr(torrent_client, 'add_magnets', boom)
    assert client.post('/api/torrents', json={'magnets': MAGNET}).status_code == 502
    assert get_db().execute('SELECT COUNT(*) c FROM torrents').fetchone()['c'] == 0


# --- control and editing ---------------------------------------------------


def test_pause_reaches_the_client(client, fake_client):
    assert client.post(f'/api/torrents/{HEX}/pause').status_code == 200
    assert fake_client['paused'] == [HEX]


def test_delete_removes_our_row_too(client, fake_client):
    client.post('/api/torrents', json={'magnets': MAGNET})
    assert client.delete(f'/api/torrents/{HEX}?deleteFiles=true').status_code == 200
    assert fake_client['deleted'] == [(HEX, True)]
    assert get_db().execute('SELECT COUNT(*) c FROM torrents').fetchone()['c'] == 0


def test_delete_defaults_to_keeping_the_files(client, fake_client):
    client.delete(f'/api/torrents/{HEX}')
    assert fake_client['deleted'] == [(HEX, False)]


def test_patch_splits_writes_between_the_client_and_our_row(client, fake_client):
    client.post('/api/torrents', json={'magnets': MAGNET})
    resp = client.patch(f'/api/torrents/{HEX}',
                        json={'note': 'keep', 'retentionDays': 14, 'ratioLimit': 2.0})
    assert resp.status_code == 200
    row = get_db().execute('SELECT * FROM torrents WHERE info_hash=?', (HEX,)).fetchone()
    assert row['note'] == 'keep' and row['retention_days'] == 14
    # The limit went to the client, and nothing mirrored it into our table.
    assert fake_client['limits'] == [{'ratio_limit': 2.0, 'seeding_minutes': None}]
    assert 'ratio_limit' not in row.keys()


def test_annotating_an_untracked_torrent_creates_a_row_for_it(client, fake_client):
    """Torrents added straight in qBittorrent still show in the tab, so they
    have to be annotatable."""
    resp = client.patch(f'/api/torrents/{HEX2}', json={'note': 'added elsewhere'})
    assert resp.status_code == 200
    row = get_db().execute('SELECT * FROM torrents WHERE info_hash=?', (HEX2,)).fetchone()
    assert row['note'] == 'added elsewhere'


def test_a_non_numeric_limit_is_a_400_not_a_500(client, fake_client):
    assert client.patch(f'/api/torrents/{HEX}', json={'dlLimit': 'fast'}).status_code == 400


# --- files -----------------------------------------------------------------


def test_file_download_refuses_a_path_outside_the_download_root(
    client, fake_client, monkeypatch, tmp_path
):
    """The client is not trusted with a path that reaches send_file."""
    monkeypatch.setenv('TORRENT_ROOT', str(tmp_path / 'dl'))
    fake_client['state']['live'] = [live(save_path='/downloads')]
    monkeypatch.setattr(torrent_client, 'torrent_files',
                        lambda h: [{'name': '../../../../etc/passwd', 'size': 1,
                                    'progress': 1.0, 'priority': 1}])
    assert client.get(f'/api/torrents/{HEX}/files/0/download').status_code == 403


def test_file_download_serves_a_finished_file(client, fake_client, monkeypatch, tmp_path):
    root = tmp_path / 'dl'
    (root / 'Debian ISO').mkdir(parents=True)
    (root / 'Debian ISO' / 'a.iso').write_bytes(b'payload')
    monkeypatch.setenv('TORRENT_ROOT', str(root))
    fake_client['state']['live'] = [live(save_path='/downloads')]
    monkeypatch.setattr(torrent_client, 'torrent_files',
                        lambda h: [{'name': 'Debian ISO/a.iso', 'size': 7,
                                    'progress': 1.0, 'priority': 1}])
    resp = client.get(f'/api/torrents/{HEX}/files/0/download')
    assert resp.status_code == 200
    assert resp.data == b'payload'
    # conditional=True: this is what makes a video seekable over Tailscale.
    assert resp.headers.get('Accept-Ranges') == 'bytes'


def test_file_download_range_request_returns_a_partial(client, fake_client, monkeypatch, tmp_path):
    root = tmp_path / 'dl'
    root.mkdir()
    (root / 'a.iso').write_bytes(b'0123456789')
    monkeypatch.setenv('TORRENT_ROOT', str(root))
    fake_client['state']['live'] = [live(save_path='/downloads')]
    monkeypatch.setattr(torrent_client, 'torrent_files',
                        lambda h: [{'name': 'a.iso', 'size': 10, 'progress': 1.0, 'priority': 1}])
    resp = client.get(f'/api/torrents/{HEX}/files/0/download',
                      headers={'Range': 'bytes=2-5'})
    assert resp.status_code == 206
    assert resp.data == b'2345'


def test_an_unfinished_file_is_a_404_not_a_crash(client, fake_client, monkeypatch, tmp_path):
    monkeypatch.setenv('TORRENT_ROOT', str(tmp_path / 'dl'))
    fake_client['state']['live'] = [live(save_path='/downloads')]
    monkeypatch.setattr(torrent_client, 'torrent_files',
                        lambda h: [{'name': 'nothing.iso', 'size': 1, 'progress': 0.1,
                                    'priority': 1}])
    assert client.get(f'/api/torrents/{HEX}/files/0/download').status_code == 404


def test_an_out_of_range_file_index_is_a_404(client, fake_client, monkeypatch):
    fake_client['state']['live'] = [live()]
    monkeypatch.setattr(torrent_client, 'torrent_files', lambda h: [])
    assert client.get(f'/api/torrents/{HEX}/files/9/download').status_code == 404


# --- categories and status -------------------------------------------------


def test_categories_come_from_the_client(client, fake_client):
    assert client.get('/api/torrents/categories').get_json() == ['audio', 'linux']


def test_a_category_needs_a_name(client, fake_client):
    assert client.post('/api/torrents/categories', json={'name': '  '}).status_code == 400


def test_status_summary_counts_errors_without_pulling_the_whole_list(client, fake_client):
    fake_client['state']['live'] = [live(state='error'), live(hash_=HEX2, state='uploading')]
    body = client.get('/api/torrents/status').get_json()
    assert body == {'available': True, 'vpnConnected': True, 'errored': 1, 'active': 1}


def test_status_summary_reports_unavailable_rather_than_failing(client, monkeypatch):
    down(monkeypatch)
    body = client.get('/api/torrents/status').get_json()
    assert body['available'] is False


def test_a_non_numeric_retention_is_a_400_not_a_500(client, fake_client):
    assert client.post('/api/torrents',
                       json={'magnets': MAGNET, 'retentionDays': 'soon'}).status_code == 400
    assert client.patch(f'/api/torrents/{HEX}',
                        json={'retentionDays': 'soon'}).status_code == 400


def test_an_omitted_retention_falls_back_to_the_configured_default(client, fake_client):
    """Absent means "use the default"; an explicit null means "keep forever".
    They are different answers, so the add box must not send a blank as null."""
    get_db().execute('UPDATE settings SET torrent_default_retention_days=14')
    get_db().commit()
    client.post('/api/torrents', json={'magnets': MAGNET})
    row = get_db().execute('SELECT retention_days FROM torrents').fetchone()
    assert row['retention_days'] == 14


def test_an_explicit_null_retention_means_keep_forever(client, fake_client):
    get_db().execute('UPDATE settings SET torrent_default_retention_days=14')
    get_db().commit()
    client.post('/api/torrents', json={'magnets': MAGNET, 'retentionDays': None})
    row = get_db().execute('SELECT retention_days FROM torrents').fetchone()
    assert row['retention_days'] is None


def test_the_client_and_vpn_urls_are_not_writable_through_settings(client):
    """They are not settings — they are where torrent/docker-compose.yml
    publishes. A writable field looks like the fix for a port conflict while
    being unable to move what Docker binds, which is exactly the confusion
    this avoids."""
    before = get_db().execute('SELECT torrent_client_url FROM settings').fetchone()[0]
    client.patch('/api/settings/ai', json={
        'torrentClientUrl': 'http://127.0.0.1:9999',
        'torrentVpnUrl': 'http://127.0.0.1:9998',
    })
    after = get_db().execute(
        'SELECT torrent_client_url, torrent_vpn_url FROM settings'
    ).fetchone()
    assert after['torrent_client_url'] == before
    assert after['torrent_vpn_url'] != 'http://127.0.0.1:9998'


def test_the_torrent_credentials_are_still_writable(client):
    """Only the URLs are locked down — the WebUI login still has to be
    settable, since qBittorrent generates it on first start."""
    client.patch('/api/settings/ai',
                 json={'torrentUsername': 'admin', 'torrentPassword': 'hunter2'})
    row = get_db().execute(
        'SELECT torrent_username, torrent_password FROM settings'
    ).fetchone()
    assert row['torrent_username'] == 'admin'
    assert row['torrent_password'] == 'hunter2'


def test_a_settings_row_stuck_on_the_broken_8080_default_is_repointed(client):
    """The column first shipped defaulting to :8080, which llama-server owns —
    Docker refuses to publish the WebUI there and the stack never starts. The
    migration repoints exactly that value and leaves a deliberate choice alone."""
    from backend.db.connection import _ensure_torrent_settings

    db = get_db()
    db.execute("UPDATE settings SET torrent_client_url='http://127.0.0.1:8080'")
    db.commit()
    _ensure_torrent_settings(db)
    assert db.execute(
        'SELECT torrent_client_url FROM settings'
    ).fetchone()[0] == 'http://127.0.0.1:8081'


def test_the_repoint_does_not_touch_a_deliberately_chosen_url(client):
    from backend.db.connection import _ensure_torrent_settings

    db = get_db()
    db.execute("UPDATE settings SET torrent_client_url='http://nas.local:9091'")
    db.commit()
    _ensure_torrent_settings(db)
    assert db.execute(
        'SELECT torrent_client_url FROM settings'
    ).fetchone()[0] == 'http://nas.local:9091'
