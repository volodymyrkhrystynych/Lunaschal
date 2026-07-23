"""main.py picks which URL PyWebView opens (and how to wait for it) based on
--dev / --server-url. start-node.sh relies on --dev --server-url together
taking priority over --server-url alone — this locks in that priority order."""
import os

import main


def test_dev_and_server_url_serves_local_vite_proxied_to_remote_backend():
    url, wait_for = main._resolve_target(dev=True, server_url='http://100.64.0.1:5000')
    assert url == main.DEV_URL
    assert wait_for == 'vite'


def test_server_url_alone_loads_the_remote_page_directly():
    url, wait_for = main._resolve_target(dev=False, server_url='http://100.64.0.1:5000')
    assert url == 'http://100.64.0.1:5000'
    assert wait_for == 'none'


def test_dev_alone_uses_local_vite_backed_by_externally_started_flask():
    url, wait_for = main._resolve_target(dev=True, server_url=None)
    assert url == main.DEV_URL
    assert wait_for == 'flask-external'


def test_neither_flag_serves_the_built_dist_via_a_spawned_flask():
    url, wait_for = main._resolve_target(dev=False, server_url=None)
    assert url == main.PROD_URL
    assert wait_for == 'flask-spawn'


def test_storage_path_is_a_stable_dir_under_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    path = main._webview_storage_path()
    assert path == os.path.join(str(tmp_path), 'lunaschal', 'webview')
    assert os.path.isdir(path)
    # Stable across calls so the same profile is reused every launch.
    assert main._webview_storage_path() == path


def test_webview_launches_non_private_with_a_persistent_profile(tmp_path, monkeypatch):
    """The Pocket runs this file (not a browser) in network mode, so the
    QtWebEngine profile must persist — private_mode=True would wipe the login
    cookie, the remembered display code, and the offline cache on every restart.
    """
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    monkeypatch.setattr(main.sys, 'argv', ['main.py', '--server-url', 'https://x.ts.net:5000'])
    monkeypatch.setattr(main.webview, 'create_window', lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(main.webview, 'start', lambda **kwargs: captured.update(kwargs))

    main.main()

    assert captured['private_mode'] is False
    assert captured['storage_path']
    assert os.path.isdir(captured['storage_path'])
