"""qBittorrent WebUI API v2 client.

Thin on purpose: this translates one HTTP call into one method and does not
model torrents. qBittorrent is the source of truth for everything about a
torrent's *state* — Lunaschal's `torrents` table only holds what qBittorrent has
no opinion about (our note, our retention policy). See backend/torrent/merge.py.

Two things this file exists to get right:

**Session handling.** The API authenticates with an SID cookie from
`/auth/login`. The cookie outlives our process only by luck, and qBittorrent
invalidates it on restart — which happens whenever the VPN container is
restarted, since the client shares its namespace. So a 403 triggers exactly one
re-login and one retry, and a caller never sees a spurious auth failure.

**Failure shape.** A container that is down and a torrent that does not exist
are different problems with different fixes, so they are different exceptions:
`TorrentClientUnavailable` becomes a 503 with "start the stack", anything else
becomes a 4xx. Everything has a timeout; without one a stopped container leaves
Flask threads blocked forever on a socket that will never answer.
"""

import threading

import requests

from backend.torrent.config import get_torrent_config

# Short enough that a dead container fails a UI poll rather than hanging it.
# The WebUI is on loopback, so a healthy call is single-digit milliseconds.
_TIMEOUT = 10
# `torrents/add` fetches the metadata for a magnet before answering.
_ADD_TIMEOUT = 30


class TorrentClientError(Exception):
    """The client answered, and said no."""


class TorrentClientUnavailable(TorrentClientError):
    """The client could not be reached at all — almost always a stopped stack."""


class _Client:
    """One session, shared. qBittorrent counts concurrent sessions, and a fresh
    login per request would both waste a round trip and slowly fill its table."""

    def __init__(self):
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._authed_url: str | None = None

    def _login(self, cfg: dict) -> None:
        base = cfg['client_url'].rstrip('/')
        try:
            resp = self._session.post(
                f'{base}/api/v2/auth/login',
                data={'username': cfg['username'], 'password': cfg['password']},
                # qBittorrent 4.5+ validates the Referer/Origin against its own
                # address; without this every write is a 403 that looks like bad
                # credentials.
                headers={'Referer': base},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            raise TorrentClientUnavailable(str(e)) from e
        if resp.status_code == 403:
            raise TorrentClientError(
                'qBittorrent refused the login — too many failed attempts, or the IP is banned.'
            )
        if resp.status_code != 200 or resp.text.strip() != 'Ok.':
            raise TorrentClientError('qBittorrent rejected the username or password.')
        self._authed_url = base

    def request(self, method: str, path: str, *, cfg: dict | None = None, **kwargs):
        cfg = cfg or get_torrent_config()
        base = cfg['client_url'].rstrip('/')
        timeout = kwargs.pop('timeout', _TIMEOUT)
        kwargs.setdefault('headers', {}).setdefault('Referer', base)

        with self._lock:
            # A changed URL in Settings means the old cookie belongs to a
            # different server; drop it rather than sending it somewhere new.
            if self._authed_url != base:
                self._session.cookies.clear()
                self._authed_url = None
            if self._authed_url is None and cfg['username']:
                self._login(cfg)

            try:
                resp = self._session.request(
                    method, f'{base}/api/v2/{path}', timeout=timeout, **kwargs
                )
                # Exactly one retry. The SID is expired or the client restarted
                # under us; a loop here would hammer a genuinely-wrong password.
                if resp.status_code == 403 and cfg['username']:
                    self._login(cfg)
                    resp = self._session.request(
                        method, f'{base}/api/v2/{path}', timeout=timeout, **kwargs
                    )
            except requests.RequestException as e:
                self._authed_url = None
                raise TorrentClientUnavailable(str(e)) from e

        if resp.status_code == 403:
            raise TorrentClientError('qBittorrent rejected the request (not authenticated).')
        if resp.status_code == 404:
            raise TorrentClientError('No such torrent.')
        if resp.status_code >= 400:
            raise TorrentClientError(resp.text.strip() or f'qBittorrent returned {resp.status_code}')
        return resp


_client = _Client()


def _get(path: str, **kwargs):
    return _client.request('GET', path, **kwargs)


def _post(path: str, **kwargs):
    return _client.request('POST', path, **kwargs)


# --- reads -----------------------------------------------------------------


def version() -> str:
    return _get('app/version').text.strip()


def torrents_info(hashes: list[str] | None = None) -> list[dict]:
    params = {'hashes': '|'.join(hashes)} if hashes else None
    return _get('torrents/info', params=params).json()


def torrent_files(info_hash: str) -> list[dict]:
    return _get('torrents/files', params={'hash': info_hash}).json()


def torrent_properties(info_hash: str) -> dict:
    return _get('torrents/properties', params={'hash': info_hash}).json()


def categories() -> dict:
    return _get('torrents/categories').json()


# --- writes ----------------------------------------------------------------


def add_magnets(urls: list[str], *, category: str | None = None, paused: bool = False) -> None:
    data = {'urls': '\n'.join(urls), 'paused': str(paused).lower()}
    if category:
        data['category'] = category
    _post('torrents/add', data=data, timeout=_ADD_TIMEOUT)


def add_files(files: list[tuple[str, bytes]], *, category: str | None = None,
              paused: bool = False) -> None:
    data = {'paused': str(paused).lower()}
    if category:
        data['category'] = category
    payload = [('torrents', (name, blob, 'application/x-bittorrent')) for name, blob in files]
    _post('torrents/add', data=data, files=payload, timeout=_ADD_TIMEOUT)


def _hashes_call(path: str, info_hash: str, **extra) -> None:
    _post(path, data={'hashes': info_hash, **extra})


def pause(info_hash: str) -> None:
    # qBittorrent 5.0 renamed pause/resume to stop/start and kept the old names
    # as aliases — but only for a while, and linuxserver ships whatever is
    # current. Try the new name, fall back to the old, so this works either
    # side of that rename instead of breaking on an image bump.
    try:
        _hashes_call('torrents/stop', info_hash)
    except TorrentClientError as e:
        if isinstance(e, TorrentClientUnavailable):
            raise
        _hashes_call('torrents/pause', info_hash)


def resume(info_hash: str) -> None:
    try:
        _hashes_call('torrents/start', info_hash)
    except TorrentClientError as e:
        if isinstance(e, TorrentClientUnavailable):
            raise
        _hashes_call('torrents/resume', info_hash)


def recheck(info_hash: str) -> None:
    _hashes_call('torrents/recheck', info_hash)


def delete(info_hash: str, *, delete_files: bool = False) -> None:
    _hashes_call('torrents/delete', info_hash, deleteFiles=str(delete_files).lower())


def set_category(info_hash: str, category: str) -> None:
    _hashes_call('torrents/setCategory', info_hash, category=category)


def set_share_limits(info_hash: str, *, ratio_limit: float | None,
                     seeding_minutes: int | None) -> None:
    """-2 means "use the global limit", -1 means "no limit". None from the UI
    means the user cleared the field, i.e. fall back to global."""
    _hashes_call(
        'torrents/setShareLimits',
        info_hash,
        ratioLimit=-2 if ratio_limit is None else ratio_limit,
        seedingTimeLimit=-2 if seeding_minutes is None else seeding_minutes,
        # Required by 4.6+; omitting it is a 400 on those builds.
        inactiveSeedingTimeLimit=-2,
    )


def set_download_limit(info_hash: str, limit_bytes: int) -> None:
    """0 is unlimited."""
    _hashes_call('torrents/setDownloadLimit', info_hash, limit=limit_bytes)


def set_upload_limit(info_hash: str, limit_bytes: int) -> None:
    _hashes_call('torrents/setUploadLimit', info_hash, limit=limit_bytes)


def create_category(name: str, save_path: str = '') -> None:
    _post('torrents/createCategory', data={'category': name, 'savePath': save_path})


def remove_category(name: str) -> None:
    _post('torrents/removeCategories', data={'categories': name})
