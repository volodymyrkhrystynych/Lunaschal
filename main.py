import sys
import threading
import time
import urllib.request
import urllib.error

import webview

FLASK_PORT = 5000
DEV_URL = 'http://localhost:5173'
PROD_URL = f'http://127.0.0.1:{FLASK_PORT}'


def _run_flask():
    import os
    from backend.app import create_app
    host = '0.0.0.0' if os.environ.get('NETWORK_MODE', '').lower() in ('1', 'true', 'yes') else '127.0.0.1'
    app = create_app()
    app.run(host=host, port=FLASK_PORT, use_reloader=False)


def _wait_for_flask(timeout: float = 10.0) -> bool:
    url = f'http://127.0.0.1:{FLASK_PORT}/api/health'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
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


def main():
    dev, server_url = _parse_args()

    if server_url:
        url = server_url
    elif dev:
        # Flask is already running via `npm run dev:flask`; just point at the Vite dev server
        if not _wait_for_flask():
            print('error: Flask did not start in time', file=sys.stderr)
            sys.exit(1)
        url = DEV_URL
    else:
        thread = threading.Thread(target=_run_flask, daemon=True)
        thread.start()

        if not _wait_for_flask():
            print('error: Flask did not start in time', file=sys.stderr)
            sys.exit(1)

        url = PROD_URL

    import os
    os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS',
        '--disable-gpu --disable-gpu-compositing --disable-vulkan '
        '--disable-background-networking --disable-sync',
    )
    webview.create_window('Lunaschal', url, width=1280, height=800, min_size=(800, 600),
                          text_select=True)
    webview.start(gui='qt')


if __name__ == '__main__':
    main()
