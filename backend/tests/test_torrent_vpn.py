"""Reading the tunnel state off gluetun's control server.

This is a report, not the kill switch — the kill switch is that the client
shares gluetun's network namespace. What it must get right is never claiming
"connected" when it does not know.
"""
import pytest
import requests

from backend.torrent import vpn


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


@pytest.fixture(autouse=True)
def no_cache():
    vpn.invalidate()
    yield
    vpn.invalidate()


def route(mapping, monkeypatch):
    def fake_get(url, timeout=None):
        for fragment, resp in mapping.items():
            if fragment in url:
                return resp
        return Resp(status=404)
    monkeypatch.setattr(requests, 'get', fake_get)


def test_reports_the_exit_ip_and_forwarded_port(monkeypatch):
    route({
        '/v1/vpn/status': Resp(payload={'status': 'running'}),
        '/v1/publicip/ip': Resp(payload={'public_ip': '185.111.110.66',
                                         'country': 'Canada', 'city': 'Toronto'}),
        '/v1/portforwarded': Resp(payload={'port': 51413}),
    }, monkeypatch)
    s = vpn.status(use_cache=False)
    assert s['connected'] is True
    assert s['ip'] == '185.111.110.66'
    assert s['forwardedPort'] == 51413


def test_falls_back_to_the_older_openvpn_era_route_names(monkeypatch):
    """gluetun renamed these across v3; pinning one spelling would break on an
    image bump."""
    route({
        '/v1/openvpn/status': Resp(payload={'status': 'running'}),
        '/v1/publicip/ip': Resp(payload={'public_ip': '1.2.3.4'}),
        '/v1/openvpn/portforwarded': Resp(payload={'port': 4242}),
    }, monkeypatch)
    s = vpn.status(use_cache=False)
    assert s['connected'] is True and s['forwardedPort'] == 4242


def test_a_dead_control_server_is_not_connected(monkeypatch):
    def boom(url, timeout=None):
        raise requests.ConnectionError('refused')
    monkeypatch.setattr(requests, 'get', boom)
    s = vpn.status(use_cache=False)
    assert s == {'available': False, 'connected': False, 'status': 'unreachable',
                 'ip': None, 'country': None, 'city': None, 'forwardedPort': None}


def test_a_tunnel_that_is_up_but_not_running_is_not_connected(monkeypatch):
    route({'/v1/vpn/status': Resp(payload={'status': 'stopped'}),
           '/v1/publicip/ip': Resp(payload={})}, monkeypatch)
    s = vpn.status(use_cache=False)
    assert s['available'] is True and s['connected'] is False


def test_no_forwarded_port_yet_reads_as_absent_not_as_port_zero(monkeypatch):
    route({'/v1/vpn/status': Resp(payload={'status': 'running'}),
           '/v1/publicip/ip': Resp(payload={'public_ip': '1.2.3.4'}),
           '/v1/portforwarded': Resp(payload={'port': 0})}, monkeypatch)
    assert vpn.status(use_cache=False)['forwardedPort'] is None


def test_the_cache_collapses_a_burst_of_polls(monkeypatch):
    calls = {'n': 0}

    def counting_get(url, timeout=None):
        calls['n'] += 1
        if '/v1/vpn/status' in url:
            return Resp(payload={'status': 'running'})
        return Resp(payload={})

    monkeypatch.setattr(requests, 'get', counting_get)
    vpn.status()
    first = calls['n']
    vpn.status()
    vpn.status()
    assert calls['n'] == first


def test_asks_for_the_current_portforward_route_first(monkeypatch):
    """gluetun serves /v1/portforward; /v1/portforwarded 404s and
    /v1/openvpn/portforwarded only works via a 301 that gluetun says stops
    being publicly reachable after v3.40."""
    asked = []

    def fake_get(url, timeout=None):
        asked.append(url.rsplit('/v1', 1)[-1])
        if '/v1/vpn/status' in url:
            return Resp(payload={'status': 'running'})
        if url.endswith('/v1/portforward'):
            return Resp(payload={'port': 35228, 'ports': [35228]})
        if '/v1/publicip/ip' in url:
            return Resp(payload={'public_ip': '1.2.3.4'})
        return Resp(status=404)

    monkeypatch.setattr(requests, 'get', fake_get)
    assert vpn.status(use_cache=False)['forwardedPort'] == 35228
    assert '/portforward' in asked
    # The deprecated spellings were never needed.
    assert '/portforwarded' not in asked


def test_a_ports_list_is_accepted_as_well_as_a_single_port(monkeypatch):
    route({'/v1/vpn/status': Resp(payload={'status': 'running'}),
           '/v1/publicip/ip': Resp(payload={'public_ip': '1.2.3.4'}),
           '/v1/portforward': Resp(payload={'ports': [35228]})}, monkeypatch)
    assert vpn.status(use_cache=False)['forwardedPort'] == 35228
