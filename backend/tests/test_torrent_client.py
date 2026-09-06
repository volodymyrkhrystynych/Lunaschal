"""qBittorrent client session handling.

The behaviour worth pinning: a 403 means the SID expired (which happens every
time the container restarts, and it shares a namespace with the VPN so that is
often), and the caller must not see it. A dead container must raise something
distinguishable from "no such torrent", because one is a 503 with a fix and the
other is a 404.
"""
import pytest
import requests

from backend.torrent import client as torrent_client

CFG = {
    'client_url': 'http://127.0.0.1:8080',
    'username': 'admin',
    'password': 'pw',
    'vpn_url': 'http://127.0.0.1:8000',
    'require_vpn': True,
    'retention_days': 0,
    'ratio_limit': None,
    'seeding_minutes': None,
}


class FakeResponse:
    def __init__(self, status=200, text='Ok.', payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    """Records calls and replays a queued script of responses."""

    def __init__(self, script=None, raise_on=None):
        self.calls = []
        self.script = list(script or [])
        self.raise_on = raise_on or set()
        self.cookies = type('C', (), {'clear': lambda self: None})()

    def _next(self, method, url):
        self.calls.append((method, url))
        if any(fragment in url for fragment in self.raise_on):
            raise requests.ConnectionError('connection refused')
        return self.script.pop(0) if self.script else FakeResponse()

    def post(self, url, **kw):
        return self._next('POST', url)

    def request(self, method, url, **kw):
        return self._next(method, url)


@pytest.fixture
def fresh():
    """A client with no cached session — the module-level one is shared."""
    return torrent_client._Client()


def test_a_dead_container_raises_unavailable_not_a_generic_error(fresh):
    fresh._session = FakeSession(raise_on={'auth/login'})
    with pytest.raises(torrent_client.TorrentClientUnavailable):
        fresh.request('GET', 'torrents/info', cfg=CFG)


def test_unavailable_is_catchable_as_a_client_error(fresh):
    """Routes catch the specific one first; anything that only knows the base
    class must still work."""
    assert issubclass(
        torrent_client.TorrentClientUnavailable, torrent_client.TorrentClientError
    )


def test_expired_session_relogs_in_once_and_the_caller_never_sees_the_403(fresh):
    fresh._session = FakeSession(script=[
        FakeResponse(),                       # initial login
        FakeResponse(status=403, text=''),    # the real call, SID expired
        FakeResponse(),                       # re-login
        FakeResponse(payload=[], text=''),    # retry succeeds
    ])
    resp = fresh.request('GET', 'torrents/info', cfg=CFG)
    assert resp.status_code == 200
    logins = [c for c in fresh._session.calls if 'auth/login' in c[1]]
    assert len(logins) == 2


def test_a_persistent_403_gives_up_rather_than_looping(fresh):
    fresh._session = FakeSession(script=[
        FakeResponse(),                    # login
        FakeResponse(status=403, text=''),  # call
        FakeResponse(),                    # re-login
        FakeResponse(status=403, text=''),  # retry, still 403
    ])
    with pytest.raises(torrent_client.TorrentClientError):
        fresh.request('GET', 'torrents/info', cfg=CFG)
    assert len([c for c in fresh._session.calls if 'auth/login' in c[1]]) == 2


def test_bad_credentials_say_so(fresh):
    fresh._session = FakeSession(script=[FakeResponse(text='Fails.')])
    with pytest.raises(torrent_client.TorrentClientError, match='username or password'):
        fresh.request('GET', 'torrents/info', cfg=CFG)


def test_a_404_becomes_no_such_torrent(fresh):
    fresh._session = FakeSession(script=[FakeResponse(), FakeResponse(status=404, text='')])
    with pytest.raises(torrent_client.TorrentClientError, match='No such torrent'):
        fresh.request('GET', 'torrents/properties', cfg=CFG)


def test_changing_the_url_in_settings_drops_the_old_cookie(fresh):
    fresh._session = FakeSession(script=[FakeResponse()] * 6)
    fresh.request('GET', 'torrents/info', cfg=CFG)
    assert fresh._authed_url == 'http://127.0.0.1:8080'
    other = {**CFG, 'client_url': 'http://192.168.1.5:8080'}
    fresh.request('GET', 'torrents/info', cfg=other)
    # Logged in again against the new host rather than sending the old SID there.
    assert fresh._authed_url == 'http://192.168.1.5:8080'
    assert len([c for c in fresh._session.calls if 'auth/login' in c[1]]) == 2


def test_pause_falls_back_to_the_pre_qbittorrent_5_endpoint(monkeypatch):
    """5.0 renamed pause/resume to stop/start. The image is `latest`, so both
    names have to work or a routine image bump breaks the buttons."""
    seen = []

    def fake_post(path, **kw):
        seen.append(path)
        if path == 'torrents/stop':
            raise torrent_client.TorrentClientError('Not Found')
        return FakeResponse()

    monkeypatch.setattr(torrent_client, '_post', fake_post)
    torrent_client.pause('abc')
    assert seen == ['torrents/stop', 'torrents/pause']


def test_pause_does_not_fall_back_when_the_container_is_simply_down(monkeypatch):
    """Retrying the old name against a dead container would turn one clear
    'stack is down' into two confusing failures."""
    def fake_post(path, **kw):
        raise torrent_client.TorrentClientUnavailable('refused')

    monkeypatch.setattr(torrent_client, '_post', fake_post)
    with pytest.raises(torrent_client.TorrentClientUnavailable):
        torrent_client.pause('abc')


def test_cleared_share_limits_send_the_use_global_sentinel(monkeypatch):
    captured = {}

    def fake_post(path, data=None, **kw):
        captured.update(data or {})
        return FakeResponse()

    monkeypatch.setattr(torrent_client, '_post', fake_post)
    torrent_client.set_share_limits('abc', ratio_limit=None, seeding_minutes=None)
    assert captured['ratioLimit'] == -2
    assert captured['seedingTimeLimit'] == -2
    # Required by qBittorrent 4.6+; omitting it is a 400 on those builds.
    assert captured['inactiveSeedingTimeLimit'] == -2
