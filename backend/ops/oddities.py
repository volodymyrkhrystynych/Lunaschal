"""Pure logic behind the briefing's "Server oddities" section.

Turns a day of `journalctl -o json` output into a short list of *kinds* of
problem, each with a stable fingerprint, so the nightly pass can tell a new
error from one that has been recurring all week. No subprocess, no DB, no
clock: `backend/oddities_job.py` owns those, the same split as
`journal_logs.py` vs `routes/logs.py`.

Three facts about this journal shaped it, all visible in a real day of it:

- **Severity is not in PRIORITY.** The app reports problems with `print()` and
  stderr logging, and systemd files both at priority 6, so nearly every record
  is "info". Severity is read from the text instead; a PRIORITY that *is* set
  (<= 4) still counts.
- **A traceback is one record per line**, and chained ones ("During handling of
  the above exception…") run to forty records. They are folded back into one
  event, with the line just before (`ERROR in app: Exception on /api/…`)
  kept as its headline, since that is the line that says which route failed.
- **The interesting frame is the last one in our code**, not the last one
  overall — that is usually inside flask, sqlite3 or the ssl module. A frame is
  ours when its path runs through `backend/`, `scripts/` or `stt/` and not
  through `site-packages`, which also makes the fingerprint the same from any
  checkout (production, a worktree, a test).

The fingerprint is what makes "recurring" a lookup rather than a judgement: a
message with its ids, paths, numbers and quoted payloads replaced by
placeholders, or, for a traceback, the exception type plus that frame.
"""

import hashlib
import re
from datetime import datetime

from backend.day_boundary import day_key_for
from backend.ops.journal_logs import looks_like_access_log

# Kinds of event. `traceback` and `error` are both failures; the split lets the
# briefing say which ones carry a frame.
TRACEBACK = 'traceback'
ERROR = 'error'
WARNING = 'warning'

TEMPLATE_MAX = 200
SAMPLE_MAX = 600

_TRACEBACK_START = 'Traceback (most recent call last):'
_CHAIN_MARKERS = (
    'During handling of the above exception, another exception occurred:',
    'The above exception was the direct cause of the following exception:',
)
_FRAME_RE = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<fn>.+)$')
# The final line of a traceback: `sqlite3.InterfaceError: bad parameter…` or a
# bare `KeyboardInterrupt`.
_EXC_LINE_RE = re.compile(r'^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<msg>.*))?$')

_ERROR_RE = re.compile(
    r'\b(error|errors|failed|failure|fails|exception|fatal|critical|unavailable|timed out)\b'
    r'|\b\w+(Error|Exception):',
    re.IGNORECASE,
)
_WARNING_RE = re.compile(r'\bwarn(ing)?\b|\b\w+Warning\b', re.IGNORECASE)
_LOG_PREFIX_RE = re.compile(r'^\[\d{4}-\d\d-\d\d [\d:,.]+\]\s*')

# Things that look like failures and are not, each of which turns up every day.
# Kept deliberately short: an entry here hides a whole kind of line forever, so
# it has to be one whose cause is known and outside the app.
_NOISE_LINES = (
    # Werkzeug's banner, printed on every start of the server.
    re.compile(r'This is a development server\. Do not use it in a production deployment'),
    # The headline werkzeug prints before a client-disconnect traceback.
    re.compile(r'^Error on request:$'),
)
# A client that hangs up mid-response (a phone locking, a tab closing) makes
# werkzeug raise from its socket write. Nothing in the app can or should fix it.
_NOISE_EXCEPTIONS = {
    'BrokenPipeError', 'ConnectionResetError', 'ConnectionAbortedError',
    'ssl.SSLEOFError', 'ssl.SSLError', 'TimeoutError',
}

_REPO_DIRS = ('backend/', 'scripts/', 'stt/')

# Normalisation, applied in order. Quoted payloads go first (a model's echoed
# JSON is the most variable thing a message can carry), then paths (which
# contain ids), then ids, so a ULID full of digits becomes one placeholder
# rather than a dozen. Digits are only replaced where they stand alone:
# `sqlite3` and `.m4a` are names, not counts.
_NORMALISERS = (
    (re.compile(r'''(['"])(?:(?!\1).){24,}?(?:\1|$)''', re.S), r'\1…\1'),
    (re.compile(r'(?:[\w.~-]*/[\w.@%+=,~-]+){2,}/?'), '<path>'),
    (re.compile(r'/mp-\w+'), '<path>'),
    (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I), '<id>'),
    (re.compile(r'\b[0-9A-HJKMNP-TV-Z]{26}\b'), '<id>'),
    (re.compile(r'\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d)?([.,]\d+)?\b'), '<time>'),
    (re.compile(r'\b0x[0-9a-f]+\b', re.I), '<hex>'),
    # Hashes, short commit ids included: a hex run with at least one digit and
    # one letter, so neither a plain number nor a word like `deadbeef` matches.
    (re.compile(r'\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{5,64}\b', re.I), '<hex>'),
    (re.compile(r'(?<![A-Za-z_])\d+(\.\d+)?'), 'N'),
    (re.compile(r'\s+'), ' '),
)


def normalise(message: str) -> str:
    text = _LOG_PREFIX_RE.sub('', message or '')
    for pattern, replacement in _NORMALISERS:
        text = pattern.sub(replacement, text)
    return text.strip()[:TEMPLATE_MAX]


def fingerprint(*parts: str) -> str:
    return hashlib.sha1('\x1f'.join(parts).encode('utf-8')).hexdigest()[:16]


def repo_relative(path: str) -> str | None:
    """`backend/fanfic/pacing.py` for any checkout's copy of it; None for a
    frame in the standard library or a dependency."""
    if 'site-packages' in path or 'dist-packages' in path:
        return None
    for marker in _REPO_DIRS:
        idx = path.rfind('/' + marker)
        if idx != -1:
            return path[idx + 1:]
        if path.startswith(marker):
            return path
    return None


def classify(message: str, priority: int = 6) -> str | None:
    """`error`, `warning`, or None for a line that is neither."""
    if any(p.search(message) for p in _NOISE_LINES):
        return None
    if _ERROR_RE.search(message):
        return ERROR
    if _WARNING_RE.search(message):
        return WARNING
    if priority <= 3:
        return ERROR
    if priority == 4:
        return WARNING
    return None


def _epoch(entry: dict) -> int | None:
    ts = entry.get('ts')
    if isinstance(ts, (int, float)):
        return int(ts)
    try:
        return int(datetime.fromisoformat(ts).timestamp())
    except (TypeError, ValueError):
        return None


class _Traceback:
    def __init__(self, headline: str | None, ts: int | None):
        self.headline = headline
        self.ts = ts
        self.lines: list[str] = []
        self.frames: list[dict] = []
        self.exc_types: list[str] = []
        self.exc_message = ''
        # True once the final exception line has been read; a chain marker
        # after it reopens the traceback.
        self.closed = False

    def add(self, message: str) -> None:
        self.lines.append(message)

    def event(self, unit: str) -> dict | None:
        exc_type = self.exc_types[-1] if self.exc_types else 'Traceback'
        if set(self.exc_types) and set(self.exc_types) <= _NOISE_EXCEPTIONS:
            return None
        ours = [f for f in self.frames if f['file']]
        frame = ours[-1] if ours else None
        where = f"{frame['file']}:{frame['function']}" if frame else (
            f"{self.frames[-1]['basename']}:{self.frames[-1]['function']}" if self.frames else '?'
        )
        # The location is kept verbatim: it is the stable part, and the path
        # normaliser would otherwise fold it into a placeholder.
        summary = f'{exc_type} in {where}'
        if self.exc_message:
            summary += f': {normalise(self.exc_message)}'
        headline = self.headline
        sample = '\n'.join(([headline] if headline else []) + self.lines)
        return {
            'unit': unit,
            'kind': TRACEBACK,
            'fingerprint': fingerprint(unit, TRACEBACK, exc_type, where),
            'template': summary[:TEMPLATE_MAX],
            'headline': headline,
            'sample': _clip_sample(sample),
            'frame': frame,
            'ts': self.ts,
        }


def _clip_sample(text: str) -> str:
    """The head and the tail of a long traceback — the request line and the
    exception are the parts worth keeping."""
    if len(text) <= SAMPLE_MAX:
        return text
    half = SAMPLE_MAX // 2
    return text[:half] + '\n…\n' + text[-half:]


def extract_events(entries: list[dict], unit: str) -> list[dict]:
    """Every error/warning/traceback in one unit's parsed journal, in order.

    `entries` is `journal_logs.parse_journal_json` output. Access-log lines are
    dropped first: they are most of the journal, and a request thread logging
    while another thread prints a traceback interleaves them.
    """
    events: list[dict] = []
    tb: _Traceback | None = None
    # The last error-looking line, kept for one record in case a traceback
    # follows it and it turns out to be that traceback's headline.
    pending: tuple[str, dict] | None = None

    def flush_pending():
        nonlocal pending
        if pending is not None:
            events.append(pending[1])
            pending = None

    def finish_tb():
        nonlocal tb
        if tb is not None:
            event = tb.event(unit)
            if event:
                events.append(event)
            tb = None

    for entry in entries:
        message = entry.get('message') or ''
        if not message.strip() or looks_like_access_log(message):
            continue

        if tb is not None:
            if message.strip() in _CHAIN_MARKERS:
                tb.add(message)
                tb.closed = False
                continue
            if message.startswith(_TRACEBACK_START):
                tb.add(message)
                continue
            if not tb.closed:
                frame = _FRAME_RE.match(message)
                if frame:
                    path = frame.group('path')
                    tb.frames.append({
                        'file': repo_relative(path),
                        'basename': path.rsplit('/', 1)[-1],
                        'line': int(frame.group('line')),
                        'function': frame.group('fn').strip(),
                    })
                    tb.add(message)
                    continue
                if message[:1].isspace():
                    tb.add(message)  # a source line or a ~~~^^^ marker
                    continue
                exc = _EXC_LINE_RE.match(message.strip())
                if exc:
                    tb.exc_types.append(exc.group('type'))
                    tb.exc_message = (exc.group('msg') or '').strip()
                    tb.add(message)
                    tb.closed = True
                    continue
            finish_tb()

        if message.startswith(_TRACEBACK_START):
            # An error line directly before the traceback is its headline, not
            # an event of its own. Werkzeug's "Error on request:" never gets
            # here: it is noise, so it was never pending.
            headline = None
            if pending is not None:
                headline = pending[0]
                pending = None
            tb = _Traceback(headline, _epoch(entry))
            tb.add(message)
            continue

        flush_pending()
        if message[:1].isspace():
            continue  # the indented half of a `warnings.warn(` report
        kind = classify(message, entry.get('priority', 6))
        if kind is None:
            continue
        pending = (message, {
            'unit': unit,
            'kind': kind,
            'fingerprint': fingerprint(unit, kind, normalise(message)),
            'template': normalise(message),
            'headline': None,
            'sample': _clip_sample(message),
            'frame': None,
            'ts': _epoch(entry),
        })

    finish_tb()
    flush_pending()
    return events


def group(events: list[dict]) -> list[dict]:
    """Collapse events into one cluster per fingerprint, most frequent first.
    Each keeps the newest sample, since that is the one worth reading."""
    clusters: dict[str, dict] = {}
    for event in events:
        fp = event['fingerprint']
        ts = event.get('ts')
        cluster = clusters.get(fp)
        if cluster is None:
            cluster = clusters[fp] = {
                'fingerprint': fp,
                'unit': event['unit'],
                'kind': event['kind'],
                'template': event['template'],
                'sample': event['sample'],
                'frame': event.get('frame'),
                'count': 0,
                'first_ts': ts,
                'last_ts': ts,
                'days': {},
            }
        cluster['count'] += 1
        if ts is not None:
            day = day_key_for(ts)
            cluster['days'][day] = cluster['days'].get(day, 0) + 1
            if cluster['first_ts'] is None or ts < cluster['first_ts']:
                cluster['first_ts'] = ts
            if cluster['last_ts'] is None or ts >= cluster['last_ts']:
                cluster['last_ts'] = ts
                cluster['sample'] = event['sample']
                cluster['frame'] = event.get('frame') or cluster['frame']
    return sorted(clusters.values(), key=lambda c: (-c['count'], c['fingerprint']))


def llm_job_events(rows) -> list[dict]:
    """Failed `llm_jobs` rows as events. A handler that fails without printing
    anything is invisible to the journal scan, and this table records it anyway."""
    events = []
    for row in rows:
        error = (row['error'] or 'failed with no message').strip()
        message = f"{row['kind']}: {error}"
        events.append({
            'unit': 'llm_jobs',
            'kind': ERROR,
            'fingerprint': fingerprint('llm_jobs', row['kind'], normalise(error)),
            'template': normalise(message),
            'headline': None,
            'sample': _clip_sample(message),
            'frame': None,
            'ts': row['finished_at'],
        })
    return events


def scan_argv(service: str, since_ts: int, until_ts: int) -> list[str]:
    """`journalctl` for one unit over an absolute window. `service` must come
    from `journal_logs.UNITS`; the times are ints this module formats itself."""
    return [
        'journalctl', '--user', '-u', service, '-o', 'json', '--no-pager',
        '--since', f'@{int(since_ts)}', '--until', f'@{int(until_ts)}',
    ]


# --- status -------------------------------------------------------------

NEW = 'new'
RECURRING = 'recurring'
QUIET = 'quiet'
REGRESSED = 'regressed'

RECURRING_MIN_DAYS = 2
QUIET_AFTER_DAYS = 7


def status_when_seen(previous: str | None, days_seen_recently: int) -> str:
    """The status of a fingerprint that turned up in this scan.

    `previous` is None for one never seen before. `days_seen_recently` counts
    the distinct days (this one included) it appeared on in the last week.
    """
    if previous is None:
        return NEW
    if previous == QUIET:
        return REGRESSED
    if days_seen_recently >= RECURRING_MIN_DAYS:
        return RECURRING
    return previous


def status_when_unseen(previous: str, last_seen: int, now: int) -> str:
    """The status of a fingerprint this scan did not see."""
    if previous != QUIET and last_seen < now - QUIET_AFTER_DAYS * 86400:
        return QUIET
    return previous
