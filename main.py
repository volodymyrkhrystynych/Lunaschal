import os
import sys
import threading
import time
import urllib.request
import urllib.error

import webview

FLASK_PORT = 5000


def _webview_storage_path() -> str:
    """Stable per-user dir for the QtWebEngine profile (cookies, localStorage,
    IndexedDB).

    PyWebView defaults to ``private_mode=True`` — an off-the-record profile whose
    storage is in-memory and wiped when the window closes. On the Pocket
    (``start-node.sh`` runs this file, not a browser) that wiped everything on
    every restart: the network-mode login cookie (forcing a fresh password +
    display-code login), the remembered display code, and the persisted offline
    React Query cache. A fixed path under XDG_DATA_HOME keeps them across
    restarts.
    """
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(
        os.path.expanduser('~'), '.local', 'share'
    )
    path = os.path.join(base, 'lunaschal', 'webview')
    os.makedirs(path, exist_ok=True)
    return path

# In network mode the dev servers speak HTTPS only (a Tailscale cert bound to
# TAILSCALE_HOSTNAME, set by start-server.sh) — plain http://localhost can no
# longer reach them, and the cert wouldn't validate for "localhost" anyway.
_NETWORK_MODE = os.environ.get('NETWORK_MODE', '').lower() in ('1', 'true', 'yes')
_TAILSCALE_HOSTNAME = os.environ.get('TAILSCALE_HOSTNAME')
if _NETWORK_MODE and _TAILSCALE_HOSTNAME:
    DEV_URL = f'https://{_TAILSCALE_HOSTNAME}:5173'
    PROD_URL = f'https://{_TAILSCALE_HOSTNAME}:{FLASK_PORT}'
    _HEALTH_URL = f'https://{_TAILSCALE_HOSTNAME}:{FLASK_PORT}/api/health'
else:
    DEV_URL = 'http://localhost:5173'
    PROD_URL = f'http://127.0.0.1:{FLASK_PORT}'
    _HEALTH_URL = f'http://127.0.0.1:{FLASK_PORT}/api/health'


def _run_flask():
    from backend.app import create_app
    host = '0.0.0.0' if _NETWORK_MODE else '127.0.0.1'
    app = create_app()
    cert, key = os.environ.get('VITE_HTTPS_CERT'), os.environ.get('VITE_HTTPS_KEY')
    ssl_context = (cert, key) if _NETWORK_MODE and cert and key else None
    app.run(host=host, port=FLASK_PORT, use_reloader=False, ssl_context=ssl_context)


def _wait_for_flask(timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(_HEALTH_URL, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


def _wait_for_vite(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(DEV_URL, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def _parse_args():
    dev = '--dev' in sys.argv
    server_url = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--server-url' and i + 1 < len(sys.argv):
            server_url = sys.argv[i + 1]
        elif arg.startswith('--server-url='):
            server_url = arg.split('=', 1)[1]
    return dev, server_url


def _resolve_target(dev: bool, server_url: str | None) -> tuple[str, str]:
    """Decide which URL PyWebView should open and how to wait for it to be ready.

    wait_for is one of:
    - 'vite': local Vite dev server, /api proxied to server_url (see start-node.sh)
    - 'none': server_url is a fully remote page — nothing local to wait for
    - 'flask-external': local Vite dev server backed by a Flask the caller already started
    - 'flask-spawn': serve the built dist/ from a Flask instance we start ourselves
    """
    if dev and server_url:
        return DEV_URL, 'vite'
    if server_url:
        return server_url, 'none'
    if dev:
        return DEV_URL, 'flask-external'
    return PROD_URL, 'flask-spawn'


def main():
    dev, server_url = _parse_args()
    url, wait_for = _resolve_target(dev, server_url)

    if wait_for == 'flask-spawn':
        thread = threading.Thread(target=_run_flask, daemon=True)
        thread.start()

    if wait_for in ('flask-spawn', 'flask-external'):
        if not _wait_for_flask():
            print('error: Flask did not start in time', file=sys.stderr)
            sys.exit(1)
    elif wait_for == 'vite':
        if not _wait_for_vite():
            print('error: Vite dev server did not start in time', file=sys.stderr)
            sys.exit(1)

    import os
    os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS',
        '--disable-gpu --disable-gpu-compositing --disable-vulkan '
        '--disable-background-networking --disable-sync',
    )
    webview.create_window('Lunaschal', url, width=1280, height=800, min_size=(800, 600),
                          text_select=True)
    # private_mode=False + a persistent storage_path so cookies/localStorage/
    # IndexedDB survive a restart (see _webview_storage_path). Without this the
    # network-mode login and all client-side persistence reset every launch.
    webview.start(gui='qt', private_mode=False, storage_path=_webview_storage_path())


if __name__ == '__main__':
    main()
