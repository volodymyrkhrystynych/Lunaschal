import os
import sys
import threading
import time
import urllib.request
import urllib.error
import base64
import binascii
from collections import deque

# webview and Qt are imported inside _start_window() rather than here: --headless
# is the production server (ops/run-prod.sh) and must not need a display, a
# QtWebEngine install, or the ~1.5G that loading it costs. The import *order*
# inside that function still matters — see the comment there.

FLASK_PORT = int(os.environ.get('LUNASCHAL_PORT', '5000'))

# The dev backend deliberately does not share :5000 with production. The
# production server now runs full-time under lunaschal.service, so a dev run on
# the same port would either fail to bind or, worse, have start.sh kill the
# running server to free it.
DEV_FLASK_PORT = int(os.environ.get('LUNASCHAL_DEV_PORT', '5001'))
ICON_PATH = os.path.join(os.path.dirname(__file__), 'public', 'icons', 'icon.png')


def _timestamp_midi_events(events) -> list[dict]:
    """Stamp a parser batch once so simultaneous chord notes stay simultaneous."""
    captured_at = time.time_ns() // 1_000_000
    result = []
    for event in events:
        value = event.as_dict()
        value['timestampMs'] = captured_at
        result.append(value)
    return result


class _DesktopApi:
    """Small JS bridge for capabilities QtWebEngine does not provide."""

    def __init__(self):
        self._midi_lock = threading.Lock()
        self._midi_events = deque(maxlen=2048)
        self._midi_stop = threading.Event()
        self._midi_thread = None
        self._midi_path = None

    def is_desktop(self) -> bool:
        return True

    def midi_devices(self) -> list[dict[str, str]]:
        from backend.piano.midi import list_raw_midi_devices

        return list_raw_midi_devices()

    def midi_open(self, path: str) -> dict:
        devices = {item['id'] for item in self.midi_devices()}
        if path not in devices:
            return {'ok': False, 'error': 'That MIDI device is not available.'}
        self.midi_close()
        self._midi_stop.clear()
        self._midi_path = path
        self._midi_thread = threading.Thread(
            target=self._read_midi,
            args=(path,),
            daemon=True,
            name='piano-midi-input',
        )
        self._midi_thread.start()
        return {'ok': True}

    def midi_poll(self) -> dict:
        with self._midi_lock:
            events = list(self._midi_events)
            self._midi_events.clear()
        return {'events': events, 'connected': self._midi_path is not None}

    def midi_close(self) -> dict:
        self._midi_stop.set()
        thread = self._midi_thread
        if thread and thread.is_alive():
            thread.join(timeout=0.3)
        self._midi_thread = None
        self._midi_path = None
        return {'ok': True}

    def _read_midi(self, path: str) -> None:
        from backend.piano.midi import MidiStreamParser

        parser = MidiStreamParser()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                while not self._midi_stop.wait(0.01):
                    try:
                        chunk = os.read(fd, 256)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        continue
                    with self._midi_lock:
                        self._midi_events.extend(
                            _timestamp_midi_events(parser.feed(chunk))
                        )
            finally:
                os.close(fd)
        except OSError as exc:
            with self._midi_lock:
                self._midi_events.append({'kind': 'error', 'message': str(exc)})
        finally:
            self._midi_path = None

    def copy_image(self, data_url: str) -> dict:
        """Put a browser-fetched image on the desktop clipboard."""
        try:
            header, encoded = data_url.split(',', 1)
            if not header.startswith('data:image/') or ';base64' not in header:
                raise ValueError('Expected a base64 image.')
            data = base64.b64decode(encoded, validate=True)

            from qtpy.QtGui import QImage
            from qtpy.QtWidgets import QApplication

            image = QImage.fromData(data)
            if image.isNull():
                raise ValueError('The picture could not be decoded.')
            app = QApplication.instance()
            if app is None:
                raise RuntimeError('The desktop application is not ready.')
            app.clipboard().setImage(image)
            return {'ok': True}
        except (ValueError, binascii.Error, RuntimeError) as exc:
            return {'ok': False, 'error': str(exc)}


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
    _DEV_HEALTH_URL = f'https://{_TAILSCALE_HOSTNAME}:{DEV_FLASK_PORT}/api/health'
else:
    DEV_URL = 'http://localhost:5173'
    PROD_URL = f'http://127.0.0.1:{FLASK_PORT}'
    _HEALTH_URL = f'http://127.0.0.1:{FLASK_PORT}/api/health'
    _DEV_HEALTH_URL = f'http://127.0.0.1:{DEV_FLASK_PORT}/api/health'


def _run_flask():
    from backend.app import create_app
    host = '0.0.0.0' if _NETWORK_MODE else '127.0.0.1'
    app = create_app()
    cert, key = os.environ.get('VITE_HTTPS_CERT'), os.environ.get('VITE_HTTPS_KEY')
    ssl_context = (cert, key) if _NETWORK_MODE and cert and key else None
    app.run(host=host, port=FLASK_PORT, use_reloader=False, ssl_context=ssl_context)


def _wait_for_flask(url: str = _HEALTH_URL, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
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
    headless = '--headless' in sys.argv
    server_url = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--server-url' and i + 1 < len(sys.argv):
            server_url = sys.argv[i + 1]
        elif arg.startswith('--server-url='):
            server_url = arg.split('=', 1)[1]
    return dev, server_url, headless


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
    dev, server_url, headless = _parse_args()

    if headless:
        # The production server (ops/run-prod.sh). Runs Flask in the foreground
        # and never returns, so systemd's Restart= governs the process lifetime.
        #
        # This exists because the windowed path exits 0 when the window is
        # closed: with Restart=on-failure that looked like a clean shutdown, so
        # closing the window silently took the LAN server down for the phone and
        # the Pocket 2 and systemd correctly declined to bring it back.
        _run_flask()
        return

    url, wait_for = _resolve_target(dev, server_url)

    if wait_for == 'flask-spawn':
        thread = threading.Thread(target=_run_flask, daemon=True)
        thread.start()

    if wait_for in ('flask-spawn', 'flask-external'):
        # --dev talks to the dev backend on DEV_FLASK_PORT. Probing FLASK_PORT
        # here would find the *production* server, report ready, and open a
        # window onto a Vite proxy whose own backend hadn't started yet.
        health = _HEALTH_URL if wait_for == 'flask-spawn' else _DEV_HEALTH_URL
        if not _wait_for_flask(health):
            print('error: Flask did not start in time', file=sys.stderr)
            sys.exit(1)
    elif wait_for == 'vite':
        if not _wait_for_vite():
            print('error: Vite dev server did not start in time', file=sys.stderr)
            sys.exit(1)

    _start_window(url)


def _patch_pywebview_qt_permission_policy():
    """pywebview's Qt backend calls setFeaturePermission(url, feature, 2) with
    a raw int rather than the QWebEnginePage.PermissionPolicy enum member —
    still true as of its current main branch, not just the pinned version
    here. Older PyQt6/sip coerced the int silently; PyQt6 6.11 doesn't, and
    the very first feature-permission request (anything outside mic/camera —
    e.g. notifications) crashes the whole process with a TypeError. Patch the
    method in place with the correctly-typed call before any window exists.
    """
    from webview.platforms.qt import BrowserView, QWebPage

    def onFeaturePermissionRequested(self, url, feature):
        granted = feature in (
            QWebPage.Feature.MediaAudioCapture,
            QWebPage.Feature.MediaVideoCapture,
            QWebPage.Feature.MediaAudioVideoCapture,
        )
        policy = (
            QWebPage.PermissionPolicy.PermissionGrantedByUser
            if granted
            else QWebPage.PermissionPolicy.PermissionDeniedByUser
        )
        self.setFeaturePermission(url, feature, policy)

    BrowserView.WebPage.onFeaturePermissionRequested = onFeaturePermissionRequested


def _start_window(url: str):
    # QtWebEngine must be imported before *any* QCoreApplication exists — Qt needs
    # to set AA_ShareOpenGLContexts while there is still no app instance. We create
    # a QApplication (for the WM identity) before PyWebView loads its qt backend,
    # so without this eager import PyWebView's
    # `from qtpy.QtWebEngineWidgets import ...` raises ImportError, it falls back
    # to PyQt5/WebKit, and startup dies with "You must have either QT or GTK with
    # Python extensions installed".
    import webview
    import qtpy.QtWebEngineWidgets  # noqa: F401  (import order matters, see above)
    from qtpy.QtWidgets import QApplication

    os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS',
        '--disable-gpu --disable-gpu-compositing --disable-vulkan '
        '--disable-background-networking --disable-sync',
    )
    _patch_pywebview_qt_permission_policy()

    # Give the Qt app a stable identity so the window manager can match the
    # .desktop entry (and set the window icon from the PNG generated from SVG).
    _qt_app = QApplication.instance()
    if _qt_app is None:
        _qt_app = QApplication(sys.argv)
    _qt_app.setApplicationName('lunaschal')
    _qt_app.setApplicationDisplayName('Lunaschal')

    webview.create_window('Lunaschal', url, width=1280, height=800, min_size=(800, 600),
                          text_select=True, js_api=_DesktopApi())
    # private_mode=False + a persistent storage_path so cookies/localStorage/
    # IndexedDB survive a restart (see _webview_storage_path). Without this the
    # network-mode login and all client-side persistence reset every launch.
    webview.start(gui='qt', private_mode=False, storage_path=_webview_storage_path(), icon=ICON_PATH)


if __name__ == '__main__':
    main()
