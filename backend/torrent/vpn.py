"""Reads the tunnel's state from gluetun's control server.

This is a *report*, not the kill switch. The kill switch is that qBittorrent
shares gluetun's network namespace and so has no route to anything but the
WireGuard tun — it holds whether or not this file works. What this buys is the
ability to say "the tunnel is down" in the UI instead of showing a swarm that
mysteriously never connects, and to refuse an add rather than queue it into a
client that cannot reach a tracker.

It also answers the question the whole feature exists for — *whose IP are the
peers seeing?* — with the actual exit address rather than an assurance.
"""

import threading
import time

import requests

from backend.torrent.config import get_torrent_config

_TIMEOUT = 5
# The list polls at 1.5s while torrents are active and each poll wants the
# banner. gluetun's control server is cheap but this is three HTTP calls, so a
# short TTL collapses a burst into one round of them without ever showing state
# old enough to matter.
_CACHE_TTL = 2.0

_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def _try_paths(base: str, paths: list[str]) -> dict | None:
    """gluetun renamed several control-server routes across v3 (the OpenVPN-era
    `/v1/openvpn/...` became `/v1/vpn/...`, and port forwarding moved twice).
    Ask for each spelling rather than pinning a build."""
    for path in paths:
        try:
            resp = requests.get(f'{base}{path}', timeout=_TIMEOUT)
        except requests.RequestException:
            return None
        if resp.status_code == 404:
            continue
        if resp.ok:
            try:
                return resp.json()
            except ValueError:
                return None
    return None


def _fetch() -> dict:
    base = get_torrent_config()['vpn_url'].rstrip('/')

    status = _try_paths(base, ['/v1/vpn/status', '/v1/openvpn/status'])
    if status is None:
        # Nothing answered: gluetun itself is down, which means the client is
        # too (it has no network without it).
        return {
            'available': False,
            'connected': False,
            'status': 'unreachable',
            'ip': None,
            'country': None,
            'city': None,
            'forwardedPort': None,
        }

    ip = _try_paths(base, ['/v1/publicip/ip']) or {}
    forwarded = _try_paths(base, ['/v1/portforwarded', '/v1/openvpn/portforwarded']) or {}

    state = (status.get('status') or '').lower()
    port = forwarded.get('port') or forwarded.get('ports')
    if isinstance(port, list):
        port = port[0] if port else None

    return {
        'available': True,
        'connected': state == 'running',
        'status': state or 'unknown',
        'ip': ip.get('public_ip'),
        'country': ip.get('country'),
        'city': ip.get('city'),
        # 0 means gluetun has not been granted one yet, which is not the same as
        # port forwarding being off — surface it as absent either way.
        'forwardedPort': port or None,
    }


def status(*, use_cache: bool = True) -> dict:
    global _cache
    now = time.monotonic()
    with _lock:
        if use_cache and _cache and now - _cache[0] < _CACHE_TTL:
            return dict(_cache[1])
    result = _fetch()
    with _lock:
        _cache = (time.monotonic(), result)
    return dict(result)


def invalidate() -> None:
    """Drop the cache — used after a settings change so the panel doesn't keep
    showing a reading taken against the old URL."""
    global _cache
    with _lock:
        _cache = None
