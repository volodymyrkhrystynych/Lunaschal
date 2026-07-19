"""backend.app._start_listener spawns stt/listener.py as a subprocess.

Flask serves HTTPS-only in network mode (start-server.sh wires in a Tailscale
cert so iOS Safari can access the mic), so the listener's default
LUNASCHAL_URL=http://127.0.0.1:5000 would get a connection reset unless it's
pointed at the HTTPS hostname instead — this locks in that env-var handoff.
"""
from backend import app as app_module


def _run_start_listener(monkeypatch, env, popen_calls):
    monkeypatch.setattr(app_module.os, 'environ', env)
    monkeypatch.setattr(app_module.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(
        app_module.subprocess, 'Popen',
        lambda args, env: popen_calls.append((args, env)) or type('P', (), {'pid': 1, 'terminate': lambda self: None})(),
    )
    monkeypatch.setattr(app_module.atexit, 'register', lambda fn: None)
    app_module._start_listener()


def test_sets_https_lunaschal_url_in_network_mode(monkeypatch):
    popen_calls = []
    env = {'STT_LISTENER': '1', 'TAILSCALE_HOSTNAME': 'kozak.tail20a78b.ts.net'}
    _run_start_listener(monkeypatch, env, popen_calls)

    assert len(popen_calls) == 1
    _, passed_env = popen_calls[0]
    assert passed_env['LUNASCHAL_URL'] == 'https://kozak.tail20a78b.ts.net:5000'


def test_leaves_explicit_lunaschal_url_untouched(monkeypatch):
    popen_calls = []
    env = {
        'STT_LISTENER': '1',
        'TAILSCALE_HOSTNAME': 'kozak.tail20a78b.ts.net',
        'LUNASCHAL_URL': 'http://100.64.0.1:5000',
    }
    _run_start_listener(monkeypatch, env, popen_calls)

    _, passed_env = popen_calls[0]
    assert passed_env['LUNASCHAL_URL'] == 'http://100.64.0.1:5000'


def test_no_tailscale_hostname_leaves_default_url_unset(monkeypatch):
    popen_calls = []
    env = {'STT_LISTENER': '1'}
    _run_start_listener(monkeypatch, env, popen_calls)

    _, passed_env = popen_calls[0]
    assert 'LUNASCHAL_URL' not in passed_env
