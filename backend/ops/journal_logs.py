"""Pure logic behind the Settings → Logs tab.

Production runs as the `systemd --user` unit `lunaschal.service` (Type=exec via
`ops/run-prod.sh`), so every line the app prints lands in the user journal. The
only way to read it used to be an SSH session and `journalctl --user -u
lunaschal`; this module is the parsing/argument core that lets the same journal
be read over the authenticated API from a phone or the Pocket 2.

Same split as `backup_status.py` vs `routes/backup.py`: everything here is
subprocess-free and clock-free enough to unit-test. `routes/logs.py` owns the
actual `journalctl` call.
"""

import json
import re

# The fixed set of units the Logs tab can read. A request names one of these
# keys; the `.service` string is never taken from user input.
UNITS: dict[str, dict[str, str]] = {
    'lunaschal': {'service': 'lunaschal.service', 'label': 'App server'},
    'lunaschal-llama': {'service': 'lunaschal-llama.service', 'label': 'llama.cpp'},
    'lunaschal-deploy': {'service': 'lunaschal-deploy.service', 'label': 'Deploy watcher'},
    'lunaschal-backup': {'service': 'lunaschal-backup.service', 'label': 'Backup job'},
}

# Keys the UI offers for `--since`; the value is what journalctl actually gets.
# The user picks a key, never types a time expression.
SINCE_PRESETS: dict[str, str] = {
    '15m': '15 min ago',
    '1h': '1 hour ago',
    '6h': '6 hours ago',
    '24h': '1 day ago',
    '3d': '3 days ago',
}

MIN_LINES = 1
MAX_LINES = 2000
DEFAULT_LINES = 500
DEFAULT_PRIORITY = 6  # journald "info", used when a record carries no PRIORITY

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
# C0 control chars except tab — werkzeug colourises its access log and journald
# keeps the raw bytes.
_CTRL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_ACCESS_LOG_RE = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP/[\d.]+" \d{3}')


def clamp_lines(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LINES
    return max(MIN_LINES, min(MAX_LINES, n))


def build_argv(service: str, *, lines=DEFAULT_LINES, priority=None, since=None) -> list[str]:
    """Assemble the `journalctl` command line.

    `service` is expected to already be a value from `UNITS[...]['service']`.
    `lines` is clamped; `priority` is only honoured as an int 0-7; `since` is
    only honoured as a key of `SINCE_PRESETS`.
    """
    argv = [
        'journalctl', '--user', '-u', service,
        '-o', 'json', '--no-pager', '-n', str(clamp_lines(lines)),
    ]
    try:
        p = int(priority)
    except (TypeError, ValueError):
        p = None
    if p is not None and 0 <= p <= 7:
        argv += ['-p', str(p)]
    if since in SINCE_PRESETS:
        argv += ['--since', SINCE_PRESETS[since]]
    return argv


def _message_text(raw) -> str:
    """journald's `MESSAGE` is a string normally, but a list of byte values
    when the line held non-UTF-8 bytes (our werkzeug output has ANSI colour
    codes). Normalise both, then strip escape/control noise."""
    if isinstance(raw, list):
        try:
            text = bytes(b & 0xFF for b in raw).decode('utf-8', 'replace')
        except (TypeError, ValueError):
            text = ''
    elif isinstance(raw, str):
        text = raw
    else:
        text = '' if raw is None else str(raw)
    text = _ANSI_RE.sub('', text)
    text = _CTRL_RE.sub('', text)
    return text.rstrip()


def _timestamp_iso(realtime_us) -> str | None:
    """`__REALTIME_TIMESTAMP` is microseconds since the epoch, as a string."""
    try:
        seconds = int(realtime_us) / 1_000_000
    except (TypeError, ValueError):
        return None
    from datetime import datetime
    return datetime.fromtimestamp(seconds).astimezone().isoformat()


def parse_journal_json(text: str) -> list[dict]:
    """Parse `journalctl -o json` output (one JSON object per line) into the
    shape the Logs tab renders. Blank and unparseable lines are skipped rather
    than failing the whole read."""
    entries: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        try:
            priority = int(obj.get('PRIORITY', DEFAULT_PRIORITY))
        except (TypeError, ValueError):
            priority = DEFAULT_PRIORITY
        priority = max(0, min(7, priority))
        entries.append({
            'ts': _timestamp_iso(obj.get('__REALTIME_TIMESTAMP')),
            'priority': priority,
            'identifier': str(obj.get('SYSLOG_IDENTIFIER') or ''),
            'message': _message_text(obj.get('MESSAGE')),
        })
    return entries


def looks_like_access_log(message: str) -> bool:
    """A werkzeug request line, e.g.
    `100.95.99.65 - - [26/Aug/2026 21:54:37] "GET /api/journal HTTP/1.1" 200 -`.
    The raw journal is mostly these; the UI hides them by default. Shared here
    so it is covered by tests alongside the parser."""
    return bool(_ACCESS_LOG_RE.search(message or ''))
