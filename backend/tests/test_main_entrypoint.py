"""main.py picks which URL PyWebView opens (and how to wait for it) based on
--dev / --server-url. start-node.sh relies on --dev --server-url together
taking priority over --server-url alone — this locks in that priority order."""
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
