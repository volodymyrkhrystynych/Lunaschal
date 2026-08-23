"""Read-only health view of the nightly backup, plus a manual trigger.

Its own blueprint rather than more of `settings.py`: nothing here touches the
`settings` table. The backup's configuration lives in `ops/backup.env` because
`ops/backup.sh` sources it directly, and that file stays the single source of
truth — this endpoint reports what the job will do, it does not decide it.

See backend/ops/backup_status.py for why staleness (rather than the exit status
of the last run) is the signal being watched.
"""

import os
import shutil
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from backend.db.connection import get_db
from backend.ops.backup_config import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    get_config,
    destination_present,
    nearest_mount_point,
    set_config,
    validate_destination,
)
from backend.ops.backup_status import (
    classify_health,
    expected_snapshot_count,
    next_run_estimate,
    parse_env,
    parse_systemctl_show,
    parse_systemd_timestamp,
    snapshot_age_days,
    snapshot_dates,
)

bp = Blueprint('backup', __name__, url_prefix='/api/backup')

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / 'ops' / 'backup.env'
_SCRIPT_PATH = _REPO_ROOT / 'ops' / 'backup.sh'
_SERVICE = 'lunaschal-backup.service'
_TIMER = 'lunaschal-backup.timer'

# A manual run rsyncs several gigabytes to a USB drive; the HTTP request that
# starts it must not wait for that. State lives in memory only — a run that was
# interrupted by a restart is not worth checkpointing, since the drive itself
# tells you whether a snapshot landed.
_run_lock = threading.Lock()
_run_state: dict = {'running': False, 'startedAt': None, 'finishedAt': None,
                    'ok': None, 'output': None}


def _read_env() -> dict[str, str]:
    try:
        return parse_env(_ENV_PATH.read_text())
    except OSError:
        return {}


def _systemctl(*args: str) -> str | None:
    """Query the user systemd manager, or None if it cannot be reached.

    Flask normally runs as a `systemd --user` unit alongside the backup timer,
    so this works in production. Under a bare shell (a dev run, a test) there is
    no user bus and every call fails — the panel degrades to the filesystem
    evidence rather than erroring, because that evidence is the part that
    actually matters.
    """
    try:
        out = subprocess.run(
            ['systemctl', '--user', *args],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 and not out.stdout.strip():
        return None
    return out.stdout


def _timer_info() -> dict:
    info: dict = {'available': False, 'enabled': None, 'lastRun': None,
                  'lastResult': None, 'nextRun': None}

    service = _systemctl('show', _SERVICE, '-p', 'ExecMainStartTimestamp',
                         '-p', 'ExecMainExitTimestamp', '-p', 'Result')
    timer = _systemctl('show', _TIMER, '-p', 'NextElapseUSecRealtime',
                       '-p', 'UnitFileState')

    if service is None and timer is None:
        info['nextRun'] = next_run_estimate(None, datetime.now())
        return info

    info['available'] = True
    if service:
        props = parse_systemctl_show(service)
        info['lastRun'] = parse_systemd_timestamp(props.get('ExecMainStartTimestamp', ''))
        info['lastResult'] = props.get('Result') or None
    if timer:
        props = parse_systemctl_show(timer)
        info['enabled'] = props.get('UnitFileState') == 'enabled'
        info['nextRun'] = parse_systemd_timestamp(props.get('NextElapseUSecRealtime', ''))

    if not info['nextRun']:
        info['nextRun'] = next_run_estimate(info['lastRun'], datetime.now())
    return info


def _dir_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _is_writable(path: Path) -> bool:
    """Whether the job could actually write here.

    `os.access(W_OK)` rather than a probe file: this runs on every panel poll,
    and creating and deleting a file in the backup destination on each one is
    both slower (a USB spinning disk) and a needless write.
    """
    return os.access(path, os.W_OK)


def _is_mount(path: Path) -> bool:
    try:
        return path.is_dir() and os.path.ismount(path)
    except OSError:
        return False


def _is_readonly_mount(path: Path) -> bool:
    """Whether the *filesystem* is mounted read-only, as opposed to merely
    being unwritable by this user.

    `os.access()` cannot tell these apart — it answers False for both — but the
    fixes are completely different (fsck and remount vs. correcting uid= in
    fstab), so the panel has to. `ST_RDONLY` is the same bit `mount` reports as
    `ro`, read from the mount itself rather than parsed out of a command's
    output.
    """
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except OSError:
        return False


@bp.get('/status')
def status():
    cfg = get_config(get_db())
    dest_raw = cfg['path']
    retention = cfg['retentionDays']

    dest = Path(dest_raw) if dest_raw else None
    # Same rule as ops/backup.sh's backup_to_hdd, via the same pure helper, so
    # the panel and the job can never disagree about whether the drive is there.
    mount_present = bool(dest and destination_present(
        dest_exists=dest.is_dir(),
        parent_exists=dest.parent.is_dir(),
        parent_is_mount=_is_mount(dest.parent),
    ))
    probe = dest if dest and dest.is_dir() else (dest.parent if dest else None)
    writable = bool(mount_present and probe and _is_writable(probe))
    readonly = bool(mount_present and probe and _is_readonly_mount(probe))
    mount_point = nearest_mount_point(dest_raw) if dest_raw else None

    names: list[str] = []
    latest_size = None
    if dest:
        db_dir = dest / 'db'
        try:
            names = [p.name for p in db_dir.iterdir() if p.is_dir()]
        except OSError:
            names = []

    dates = snapshot_dates(names)
    latest = dates[-1] if dates else None
    if dest and latest:
        try:
            latest_size = (dest / 'db' / latest / 'lunaschal.db').stat().st_size
        except OSError:
            latest_size = None

    today = date.today()
    health, problems = classify_health(
        configured=bool(dest_raw),
        mount_present=mount_present,
        writable=writable,
        mount_readonly=readonly,
        latest_snapshot=latest,
        today=today,
    )

    free_bytes = total_bytes = None
    if mount_present and probe:
        try:
            usage = shutil.disk_usage(probe)
            free_bytes, total_bytes = usage.free, usage.total
        except OSError:
            pass

    # The media mirror's size is deliberately not computed: `du` over ~6G of
    # small files on a USB drive takes seconds, and this endpoint is polled.
    # The directory's mtime answers the question the panel actually asks —
    # "did anything land recently?" — for the cost of one stat().
    media_dir = dest / 'media' if dest else None

    return jsonify({
        'configured': bool(dest_raw),
        'destination': dest_raw or None,
        'mountPoint': mount_point,
        'configSource': cfg['source'],
        'retentionDays': retention,
        'health': health,
        'problems': problems,
        'mount': {
            'present': mount_present,
            'writable': writable,
            'readonly': readonly,
            'freeBytes': free_bytes,
            'totalBytes': total_bytes,
        },
        'snapshots': {
            'latest': latest,
            'ageDays': snapshot_age_days(latest, today),
            'count': len(dates),
            'expectedCount': expected_snapshot_count(dates, retention, today),
            'latestSizeBytes': latest_size,
            'dates': dates,
        },
        'media': {
            'lastModified': _dir_mtime(media_dir) if media_dir else None,
        },
        'timer': _timer_info(),
        'run': dict(_run_state),
    })


@bp.get('/config')
def get_backup_config():
    return jsonify(get_config(get_db()))


@bp.put('/config')
def put_backup_config():
    """Set the destination and/or retention. Settings is the source of truth.

    A path that is merely *unreachable right now* is accepted, not rejected —
    the drive is often unplugged when you are configuring this, and refusing to
    save would make the panel unusable exactly when you are trying to fix it.
    `/status` is what reports reachability.
    """
    body = request.get_json(silent=True) or {}
    path = body.get('destination')
    retention = body.get('retentionDays')

    if path is not None:
        problem = validate_destination(str(path))
        if problem:
            return jsonify({'error': problem}), 400

    if retention is not None:
        try:
            retention = int(retention)
        except (TypeError, ValueError):
            return jsonify({'error': 'Retention must be a whole number of days.'}), 400
        if not MIN_RETENTION_DAYS <= retention <= MAX_RETENTION_DAYS:
            return jsonify({
                'error': f'Retention must be between {MIN_RETENTION_DAYS} and '
                         f'{MAX_RETENTION_DAYS} days.'
            }), 400

    try:
        cfg = set_config(
            get_db(),
            path=None if path is None else str(path),
            retention_days=retention,
        )
    except ValueError:
        return jsonify({'error': 'Settings row missing'}), 500
    return jsonify(cfg)


# Directory listings for the folder picker. Capped because a browse of
# something like /nix/store should not try to serialize a million names.
_MAX_BROWSE_ENTRIES = 500


@bp.get('/browse')
def browse():
    """List the subdirectories of `path`, for choosing a backup destination.

    Folders only, and never file contents — this exists to pick a directory,
    so there is no reason for it to be able to read anything. Unreadable
    subdirectories are listed but flagged rather than omitted: a drive whose
    permissions are wrong is precisely what the user may be here to diagnose,
    and hiding it would be the unhelpful answer.
    """
    raw = request.args.get('path') or '/'
    try:
        target = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return jsonify({'error': 'Bad path'}), 400

    if not target.is_dir():
        return jsonify({'error': 'Not a directory'}), 404

    entries = []
    truncated = False
    try:
        for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if item.name.startswith('.'):
                continue
            try:
                if not item.is_dir():
                    continue
            except OSError:
                continue
            if len(entries) >= _MAX_BROWSE_ENTRIES:
                truncated = True
                break
            entries.append({
                'name': item.name,
                'path': str(item),
                'writable': _is_writable(item),
            })
    except PermissionError:
        return jsonify({'error': f'Permission denied reading {target}'}), 403
    except OSError as exc:
        return jsonify({'error': str(exc)}), 400

    return jsonify({
        'path': str(target),
        'parent': None if target.parent == target else str(target.parent),
        'entries': entries,
        'truncated': truncated,
        'writable': _is_writable(target),
        'isMount': _is_mount(target),
        # Where removable drives actually turn up, so the picker can offer them
        # instead of making the user walk down from / every time.
        'suggestions': _browse_suggestions(),
    })


def _browse_suggestions() -> list[dict]:
    candidates = ['/media', '/mnt', '/run/media', str(Path.home())]
    user = os.environ.get('USER')
    if user:
        candidates.insert(2, f'/run/media/{user}')
    seen, out = set(), []
    for c in candidates:
        p = Path(c)
        if c in seen or not p.is_dir():
            continue
        seen.add(c)
        out.append({'name': c, 'path': c})
    return out


def _run_backup() -> None:
    global _run_state
    try:
        out = subprocess.run(
            [str(_SCRIPT_PATH)], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=3600,
        )
        ok = out.returncode == 0
        # The script's own log lines are the useful output; its stderr carries
        # rsync's errors. Keep the tail of both — a full-media first run prints
        # far more than a panel should hold.
        output = (out.stdout + out.stderr).strip()[-4000:]
    except subprocess.TimeoutExpired:
        ok, output = False, 'Backup timed out after 1 hour.'
    except OSError as exc:
        ok, output = False, f'Could not start ops/backup.sh: {exc}'

    with _run_lock:
        _run_state.update({
            'running': False,
            'finishedAt': datetime.now().isoformat(),
            'ok': ok,
            'output': output,
        })


@bp.post('/run')
def run_now():
    """Run `ops/backup.sh` directly rather than via `systemctl --user start`.

    Going through systemd would be tidier, but it gives back nothing except an
    exit code in the journal — and the whole reason this panel exists is that a
    successful exit code was hiding a dead backup. Running the script here
    captures its log lines so the UI can show what it actually did.
    """
    if not _SCRIPT_PATH.exists():
        return jsonify({'error': 'ops/backup.sh not found'}), 500

    with _run_lock:
        if _run_state['running']:
            return jsonify({'error': 'A backup is already running'}), 409
        _run_state.update({
            'running': True,
            'startedAt': datetime.now().isoformat(),
            'finishedAt': None,
            'ok': None,
            'output': None,
        })

    threading.Thread(target=_run_backup, daemon=True).start()
    return jsonify(dict(_run_state))
