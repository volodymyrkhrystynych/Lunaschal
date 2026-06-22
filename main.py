import sys
import threading
import time
import urllib.request
import urllib.error

import webview

from backend.app import create_app

FLASK_PORT = 5000
DEV_URL = 'http://localhost:5173'
PROD_URL = f'http://127.0.0.1:{FLASK_PORT}'


def _run_flask():
    app = create_app()
    app.run(port=FLASK_PORT, use_reloader=False)


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


def main():
    dev = '--dev' in sys.argv

    thread = threading.Thread(target=_run_flask, daemon=True)
    thread.start()

    if not _wait_for_flask():
        print('error: Flask did not start in time', file=sys.stderr)
        sys.exit(1)

    url = DEV_URL if dev else PROD_URL
    webview.create_window('Lunaschal', url, width=1280, height=800, min_size=(800, 600))
    webview.start()


if __name__ == '__main__':
    main()
