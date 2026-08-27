"""Read-only view of the systemd --user journals, for the Settings → Logs tab.

Its own blueprint rather than more of `settings.py`: nothing here touches the
`settings` table. It shells out to `journalctl` and nothing else.

Production Flask runs as a `systemd --user` unit, so `journalctl --user` works
there. Under a bare shell (a dev run, a test) there is no user bus and the
calls fail — the endpoint then reports `available: false` with a note rather
than erroring, the same way the Backup panel degrades to filesystem evidence.
"""

import subprocess

from flask import Blueprint, jsonify, request

from backend.ops.journal_logs import UNITS, build_argv, parse_journal_json

bp = Blueprint('logs', __name__, url_prefix='/api/logs')

_UNAVAILABLE_NOTE = (
    "Server logs aren't readable here — this host has no systemd --user "
    "journal (expected on a dev run; production reads fine)."
)


def _journalctl(argv: list[str]) -> str | None:
    """Run `journalctl ...`, or None if it cannot be run at all.

    Mirrors `routes/backup.py::_systemctl`: a missing binary or a failed call
    with no output means "no journal here", which the caller turns into a
    graceful `available: false` rather than a 500.
    """
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 and not out.stdout.strip():
        return None
    return out.stdout


def _systemctl(*args: str) -> str | None:
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


@bp.get('/units')
def units():
    """The fixed unit allowlist, each flagged with whether systemd knows it."""
    out = []
    for uid, info in UNITS.items():
        shown = _systemctl('show', info['service'], '-p', 'LoadState')
        available = bool(shown and 'LoadState=loaded' in shown)
        out.append({'id': uid, 'label': info['label'], 'available': available})
    return jsonify(out)


@bp.get('')
def read_logs():
    unit = request.args.get('unit', 'lunaschal')
    if unit not in UNITS:
        return jsonify({'error': f'Unknown unit: {unit}'}), 400

    argv = build_argv(
        UNITS[unit]['service'],
        lines=request.args.get('lines'),
        priority=request.args.get('priority'),
        since=request.args.get('since'),
    )
    raw = _journalctl(argv)
    if raw is None:
        return jsonify({
            'available': False,
            'unit': unit,
            'entries': [],
            'note': _UNAVAILABLE_NOTE,
        })
    return jsonify({
        'available': True,
        'unit': unit,
        'entries': parse_journal_json(raw),
        'note': None,
    })
