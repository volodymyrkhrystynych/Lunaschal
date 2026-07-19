"""Unit tests for `backend.auth.is_localhost`.

Covers the plain localhost/127.0.0.1 case plus the desktop-app case: once
network mode serves HTTPS over the Tailscale hostname (required for iOS
Safari's navigator.mediaDevices), the desktop app no longer requests
"localhost" literally, so it's recognized instead by its own Tailscale IP.
"""
from types import SimpleNamespace

from backend import auth


def _request(host: str, remote_addr: str = '1.2.3.4'):
    return SimpleNamespace(host=host, remote_addr=remote_addr)


def test_localhost_hostnames_are_local(monkeypatch):
    monkeypatch.setattr(auth, '_SELF_TAILSCALE_IPS', set())
    assert auth.is_localhost(_request('localhost:5000'))
    assert auth.is_localhost(_request('127.0.0.1:5000'))


def test_own_tailscale_ip_is_local(monkeypatch):
    monkeypatch.setattr(auth, '_SELF_TAILSCALE_IPS', {'100.95.99.65'})
    req = _request('kozak.tail20a78b.ts.net:5000', remote_addr='100.95.99.65')
    assert auth.is_localhost(req)


def test_other_devices_are_not_local(monkeypatch):
    monkeypatch.setattr(auth, '_SELF_TAILSCALE_IPS', {'100.95.99.65'})
    req = _request('kozak.tail20a78b.ts.net:5000', remote_addr='100.105.26.15')
    assert not auth.is_localhost(req)
