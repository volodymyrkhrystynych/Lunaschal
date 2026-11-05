"""The nightly server-oddities pass, and the section it adds to the briefing.

Runs in the briefing thread, after the life-wiki pass and before the briefing
(backend/briefing_scheduler.py), so the night's own scheduled work — titling,
repo scans, the wiki — has already happened and its failures are in the window.
A plain function like the other two, so tests and `--dry-run` call it directly.

**Code finds the problems; the model only reads them.** Collecting and counting
is backend/ops/oddities.py, state is three tables, and the model is asked for
one note per *new* problem and never asked to revise one. The briefing section
is rendered from rows, so it is the same whether the model was up or not.

**A quiet morning still says so.** The section is always there: a list, a single
"none in the past day", or a line saying the check didn't run or couldn't read
the journal. Leaving it out on a clean day would make a clean day and a broken
scan look identical.

Run `python -m backend.oddities_job --dry-run` to see what a scan of the real
journal would record, without writing anything.
"""
import json
import sqlite3
import time

from ulid import ULID

from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.ops import oddities as od
from backend.ops.journal_logs import UNITS, parse_journal_json

DEFAULT_WINDOW = 24 * 3600
MAX_WINDOW = 36 * 3600
# How stale the newest scan may be before the briefing says the check did not run.
REPORT_MAX_AGE = 36 * 3600
PRUNE_AFTER_DAYS = 30
BRIEFING_LIMIT = 5

_STATUS_ORDER = {od.REGRESSED: 0, od.NEW: 1, od.RECURRING: 2}


def _journalctl(argv):
    # The Logs tab's runner: a missing binary or no user bus is None, not an error.
    from backend.routes.logs import _journalctl as run
    return run(argv)


def read_journal(since_ts: int, until_ts: int) -> tuple[list[dict], bool]:
    """Every event in the four units' journals over the window, and whether any
    journal could be read at all."""
    events, available = [], False
    for unit, info in UNITS.items():
        raw = _journalctl(od.scan_argv(info['service'], since_ts, until_ts))
        if raw is None:
            continue
        available = True
        events += od.extract_events(parse_journal_json(raw), unit)
    return events, available


def llm_job_errors(db, since_ts: int, until_ts: int) -> list[dict]:
    rows = db.execute(
        "SELECT kind, error, finished_at FROM llm_jobs WHERE status = 'error'"
        ' AND finished_at >= ? AND finished_at < ?',
        (since_ts, until_ts),
    ).fetchall()
    return od.llm_job_events(rows)


def _window(db, now: int) -> tuple[int, int]:
    """From where the last scan stopped, but never more than MAX_WINDOW back —
    a server that was off for a week should not report the week as one day."""
    try:
        row = db.execute('SELECT MAX(until_ts) AS t FROM server_oddity_scans').fetchone()
    except sqlite3.OperationalError:
        row = None  # --dry-run against a database the server has not migrated yet
    last = row['t'] if row and row['t'] else None
    since = last if last is not None else now - DEFAULT_WINDOW
    return max(since, now - MAX_WINDOW), now


def _days_seen(db, fingerprint: str, now: int) -> int:
    floor = day_key_for(now - (od.QUIET_AFTER_DAYS - 1) * 86400)
    return db.execute(
        'SELECT COUNT(*) FROM server_oddity_days WHERE fingerprint = ? AND day_key >= ?',
        (fingerprint, floor),
    ).fetchone()[0]


def record(db, clusters: list[dict], now: int) -> None:
    """Fold one scan's clusters into the tables and move every status."""
    seen = set()
    for c in clusters:
        fp = c['fingerprint']
        seen.add(fp)
        prev = db.execute(
            'SELECT status FROM server_oddities WHERE fingerprint = ?', (fp,)
        ).fetchone()
        first, last = c['first_ts'] or now, c['last_ts'] or now
        frame = json.dumps(c['frame']) if c.get('frame') else None
        if prev is None:
            db.execute(
                'INSERT INTO server_oddities (fingerprint, unit, kind, template, sample, frame,'
                ' status, total_count, last_scan_count, first_seen, last_seen)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (fp, c['unit'], c['kind'], c['template'], c['sample'], frame,
                 od.NEW, c['count'], c['count'], first, last),
            )
        else:
            db.execute(
                'UPDATE server_oddities SET template = ?, sample = ?, frame = COALESCE(?, frame),'
                ' total_count = total_count + ?, last_scan_count = ?,'
                ' last_seen = MAX(last_seen, ?) WHERE fingerprint = ?',
                (c['template'], c['sample'], frame, c['count'], c['count'], last, fp),
            )
        for day, count in c['days'].items():
            db.execute(
                'INSERT INTO server_oddity_days (fingerprint, day_key, count) VALUES (?,?,?)'
                ' ON CONFLICT(fingerprint, day_key) DO UPDATE SET count = count + excluded.count',
                (fp, day, count),
            )
        status = od.status_when_seen(prev['status'] if prev else None, _days_seen(db, fp, now))
        if status == od.REGRESSED:
            # Back after a quiet week: whatever the old note said may no longer
            # be true, so it is the one time a note is written again.
            db.execute(
                'UPDATE server_oddities SET status = ?, note = NULL, note_written_at = NULL'
                ' WHERE fingerprint = ?', (status, fp),
            )
        else:
            db.execute('UPDATE server_oddities SET status = ? WHERE fingerprint = ?', (status, fp))

    for row in db.execute(
        'SELECT fingerprint, status, last_seen FROM server_oddities'
    ).fetchall():
        if row['fingerprint'] in seen:
            continue
        db.execute(
            'UPDATE server_oddities SET status = ?, last_scan_count = 0 WHERE fingerprint = ?',
            (od.status_when_unseen(row['status'], row['last_seen'], now), row['fingerprint']),
        )

    cutoff = now - PRUNE_AFTER_DAYS * 86400
    db.execute('DELETE FROM server_oddity_days WHERE day_key < ?', (day_key_for(cutoff),))
    db.execute('DELETE FROM server_oddity_scans WHERE created_at < ?', (cutoff,))


def _current(db, now: int, *, limit: int | None = None, unannotated: bool = False) -> list[dict]:
    """What the newest scan saw, most pressing first."""
    sql = 'SELECT * FROM server_oddities WHERE last_scan_count > 0'
    if unannotated:
        sql += ' AND note IS NULL'
    rows = db.execute(sql).fetchall()
    items = [{
        'fingerprint': r['fingerprint'],
        'unit': r['unit'],
        'kind': r['kind'],
        'template': r['template'],
        'sample': r['sample'],
        'frame': json.loads(r['frame']) if r['frame'] else None,
        'status': r['status'],
        'count': r['last_scan_count'],
        'days': _days_seen(db, r['fingerprint'], now),
        'note': r['note'],
    } for r in rows]
    items.sort(key=lambda i: (_STATUS_ORDER.get(i['status'], 3), -i['count'], i['fingerprint']))
    return items[:limit] if limit else items


def annotate_new(db, now: int) -> int:
    """Notes for whatever the scan saw that has none yet. A model that is off,
    paused or broken costs only the notes: they are tried again tomorrow."""
    from backend.ai import oddities as ai
    from backend.ai.provider import is_ai_configured

    pending = _current(db, now, limit=ai.MAX_ITEMS, unannotated=True)
    if not pending or not is_ai_configured():
        return 0
    try:
        notes = ai.annotate(pending)
    except Exception as e:
        print(f'Server oddities: notes skipped ({e})')
        return 0
    for fp, note in notes.items():
        db.execute(
            'UPDATE server_oddities SET note = ?, note_written_at = ? WHERE fingerprint = ?',
            (note, now, fp),
        )
    return len(notes)


def scan(now: int | None = None, db=None) -> dict:
    """Read the window and return what was found, writing nothing."""
    now = now if now is not None else int(time.time())
    db = db if db is not None else get_db()
    since, until = _window(db, now)
    events, available = read_journal(since, until)
    events += llm_job_errors(db, since, until)
    return {
        'since': since, 'until': until, 'journalAvailable': available,
        'events': len(events), 'clusters': od.group(events),
    }


def run_oddities_pass(now: int | None = None) -> dict:
    now = now if now is not None else int(time.time())
    db = get_db()
    found = scan(now)
    record(db, found['clusters'], now)
    db.execute(
        'INSERT INTO server_oddity_scans (id, since_ts, until_ts, journal_available,'
        ' event_count, created_at) VALUES (?,?,?,?,?,?)',
        (str(ULID()), found['since'], found['until'], int(found['journalAvailable']),
         found['events'], now),
    )
    db.commit()
    # After the commit: the counts are the record, the notes only decorate it.
    notes = annotate_new(db, now)
    db.commit()
    return {
        'clusters': len(found['clusters']), 'events': found['events'],
        'journalAvailable': found['journalAvailable'], 'notes': notes,
    }


# --- the briefing section ------------------------------------------------


def briefing_report(now: int | None = None) -> dict:
    """What the briefing should say, read from the newest scan."""
    now = now if now is not None else int(time.time())
    db = get_db()
    scan_row = db.execute(
        'SELECT * FROM server_oddity_scans ORDER BY until_ts DESC LIMIT 1'
    ).fetchone()
    if scan_row is None or scan_row['until_ts'] < now - REPORT_MAX_AGE:
        return {'ran': False, 'journalAvailable': False, 'items': [], 'more': 0}
    items = _current(db, now)
    return {
        'ran': True,
        'journalAvailable': bool(scan_row['journal_available']),
        'items': items[:BRIEFING_LIMIT],
        'more': max(0, len(items) - BRIEFING_LIMIT),
    }


def _badge(item: dict) -> str:
    if item['status'] == od.REGRESSED:
        return '**Back**'
    if item['status'] == od.NEW:
        return '**New**'
    if item['days'] >= od.RECURRING_MIN_DAYS:
        return f"**Recurring** ({item['days']} of {od.QUIET_AFTER_DAYS} days)"
    return '**Ongoing**'


def render_markdown(report: dict) -> str:
    """Pure: the section appended to the briefing message."""
    if not report['ran']:
        return ("**Server oddities:** the overnight check didn't run, "
                "so the server logs weren't looked at.")
    footnote = ('' if report['journalAvailable'] else
                "_The server journal couldn't be read, so only failed background "
                "jobs were checked._")
    if not report['items']:
        line = '**Server oddities:** none in the past day.'
        return f'{line}\n{footnote}' if footnote else line
    lines = ['**Server oddities (past day)**']
    for item in report['items']:
        template = item['template'].replace('`', "'")
        lines.append(f"- {_badge(item)} · {item['unit']} · {item['count']}× — `{template}`")
        if item.get('note'):
            lines.append(f"  {item['note']}")
    if report['more']:
        lines.append(f"- …and {report['more']} more.")
    if footnote:
        lines.append(footnote)
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--dry-run', action='store_true',
                        help='scan and print; write nothing and call no model')
    args = parser.parse_args()
    if args.dry_run:
        # Read-only, so a dry run against the production database can neither
        # write to it nor run the schema on it.
        from backend.db.connection import _DB_PATH
        ro = sqlite3.connect(f'file:{_DB_PATH}?mode=ro', uri=True)
        ro.row_factory = sqlite3.Row
        found = scan(db=ro)
        print(f"{found['events']} events, {len(found['clusters'])} kinds, journal "
              f"{'read' if found['journalAvailable'] else 'unavailable'}")
        for c in found['clusters']:
            where = f" [{c['frame']['file']}:{c['frame']['line']}]" if c.get('frame') else ''
            print(f"{c['count']:5d}× {c['unit']:<16} {c['kind']:<9} {c['template'][:120]}{where}")
    else:
        print(run_oddities_pass())
