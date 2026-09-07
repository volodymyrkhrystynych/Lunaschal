"""Global screenshot capture with a durable, idempotent journal upload queue."""
import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading

from ulid import ULID

logger = logging.getLogger(__name__)


def focused_monitor():
    """Resolve Hyprland's focused output; never fall back to all monitors."""
    result = subprocess.run(['hyprctl', '-j', 'monitors'], check=True,
                            timeout=5, capture_output=True, text=True)
    monitors = json.loads(result.stdout)
    names = [monitor.get('name') for monitor in monitors
             if monitor.get('focused') is True]
    if len(names) != 1 or not isinstance(names[0], str) or not names[0]:
        raise RuntimeError('Could not identify the focused monitor.')
    return names[0]


def notify(message):
    logger.info('%s', message)
    if shutil.which('notify-send'):
        try:
            subprocess.run(['notify-send', 'Lunaschal', message], timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


class ScreenshotJournal:
    def __init__(self, session, url, root=None):
        self.session = session
        self.url = url.rstrip('/')
        self.root = Path(root) if root else Path(
            os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))
        ) / 'lunaschal/screenshots'
        self.capture_lock = threading.Lock()
        self.upload_lock = threading.Lock()

    def capture(self):
        # Multiple keyboards can emit the same shortcut; never overlap captures.
        if not self.capture_lock.acquire(blocking=False):
            return
        temporary = None
        try:
            if not shutil.which('grim'):
                raise RuntimeError('Install grim to capture the Wayland desktop.')
            output = focused_monitor()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            ident = str(ULID())
            temporary = self.root / f'{ident}.part'
            # Reserve a private file before invoking the capture tool.
            temporary.touch(mode=0o600)
            subprocess.run(['grim', '-o', output, '-t', 'png', str(temporary)],
                           check=True, timeout=15, capture_output=True)
            with temporary.open('rb') as file:
                if file.read(8) != b'\x89PNG\r\n\x1a\n':
                    raise RuntimeError('Screen capture did not produce a PNG.')
                os.fsync(file.fileno())
            temporary.rename(self.root / f'{ident}.png')
            notify('Screenshot captured. Saving to journal…')
        except Exception as exc:
            logger.exception('Screenshot capture failed')
            notify(f'Screenshot failed: {exc}')
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.capture_lock.release()
        self.flush()

    def flush(self):
        if not self.upload_lock.acquire(blocking=False):
            return
        try:
            for path in sorted(self.root.glob('*.png')):
                try:
                    ident = path.stem
                    captured = datetime.datetime.fromtimestamp(
                        path.stat().st_mtime).astimezone().isoformat(timespec='seconds')
                    response = self.session.post(
                        f'{self.url}/api/journal',
                        json={'id': ident, 'title': 'Screenshot',
                              'content': f'Screenshot captured {captured}',
                              'pendingAttachments': 1}, timeout=15)
                    response.raise_for_status()
                    with path.open('rb') as file:
                        response = self.session.post(
                            f'{self.url}/api/journal/{ident}/attachments',
                            data={'attachmentId': ident, 'name': 'Screenshot'},
                            files={'file': ('screenshot.png', file, 'image/png')},
                            timeout=30)
                    response.raise_for_status()
                    path.unlink()
                    notify('Screenshot saved to journal.')
                except Exception:
                    logger.exception('Screenshot upload failed; retaining %s', path)
                    notify('Screenshot kept locally. Journal upload will retry.')
                    break
        finally:
            self.upload_lock.release()

    def retry_loop(self):
        while True:
            self.flush()
            threading.Event().wait(60)
