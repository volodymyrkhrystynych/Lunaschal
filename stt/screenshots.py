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


def _live_instance(hypr_dir, prefer=None):
    """Return (signature, wayland socket) of a running Hyprland, or None.

    Each instance directory holds a `hyprland.lock` whose first line is the
    compositor's pid and second its Wayland socket name. A directory left by a
    crashed session has a dead pid, so it is skipped rather than trusted.
    """
    try:
        dirs = sorted((d for d in hypr_dir.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return None
    dirs.sort(key=lambda d: d.name != prefer)
    for instance in dirs:
        try:
            lines = (instance / 'hyprland.lock').read_text().split()
        except OSError:
            continue
        if (len(lines) >= 2 and lines[0].isdigit()
                and Path(f'/proc/{lines[0]}').exists()
                and (instance / '.socket.sock').exists()):
            return instance.name, lines[1]
    return None


def hyprland_env(environ=None):
    """The environment hyprctl and grim need, recovered if it was never inherited.

    lunaschal.service starts at boot, seconds before Hyprland exports
    HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY to the systemd user
    environment, so the listener it spawns has neither and every capture failed
    until the service happened to be restarted after login. The same goes for a
    Hyprland restarted under a running service, whose old signature is stale.
    """
    env = dict(os.environ if environ is None else environ)
    runtime = Path(env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
    hypr_dir = runtime / 'hypr'
    current = env.get('HYPRLAND_INSTANCE_SIGNATURE')
    if (current and env.get('WAYLAND_DISPLAY')
            and (hypr_dir / current / '.socket.sock').exists()):
        return env
    found = _live_instance(hypr_dir, prefer=current)
    if found:
        env['HYPRLAND_INSTANCE_SIGNATURE'], env['WAYLAND_DISPLAY'] = found
    return env


def focused_monitor(env=None):
    """Resolve Hyprland's focused output; never fall back to all monitors."""
    result = subprocess.run(['hyprctl', '-j', 'monitors'], check=True,
                            timeout=5, capture_output=True, text=True,
                            env=env)
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
            env = hyprland_env()
            output = focused_monitor(env)
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            ident = str(ULID())
            temporary = self.root / f'{ident}.part'
            # Reserve a private file before invoking the capture tool.
            temporary.touch(mode=0o600)
            subprocess.run(['grim', '-o', output, '-t', 'png', str(temporary)],
                           check=True, timeout=15, capture_output=True, env=env)
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
                    with path.open('rb') as file:
                        response = self.session.post(
                            f'{self.url}/api/journal/screenshots',
                            data={'attachmentId': ident, 'capturedAt': captured},
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
