"""Applying triage to the database: the free sweep, and the model worker.

`triage.py` is pure and `ai/job_triage.py` is one model call; this is the half
that reads rows, decides which layer each one needs, and writes the verdict
back. The split is the same one `linkage.py` and `linker.py` already use.

Two loops, because the two layers cost wildly different things:

- **`run_gate_sweep`** applies the pure title gate to every pending row at
  once. No model, no network, no clock — a few thousand rows is milliseconds,
  so the scheduler runs it every tick and the obvious noise never survives long
  enough to reach the model.
- **`drain_once`** is a single-slot worker for what the gate could not decide.
  It is the only part that touches the model, so it is the only part that
  defers through `backend/ai/priority.py` — the moment-to-moment yielding
  `research_scheduler` does, rather than an hour window. A posting synced at
  09:00 should not sit unsummarised until 02:00.

Measured at 3–8 seconds a posting against a 10,000-character description, which
is what makes judging every new posting affordable at all. A backlog of ~1,300
drains in a couple of hours, and a nightly delta is a couple of minutes.

The rules this lives by are `queue.py`'s, for `queue.py`'s reasons:

- **Never hold a transaction across the model call.** `get_db()` is one
  process-global connection, so a `commit()` in any Flask handler commits
  whatever this thread left pending. Read inputs, commit, call the model,
  commit the result.
- **A failure is recorded, not swallowed.** `triage_error` is written and the
  row is then skipped, so one posting the model chokes on cannot sit at the
  head of the queue being retried every five minutes while nothing behind it
  is ever judged.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='jobtriage')
_lock = threading.Lock()
_current: dict | None = None
_last: dict | None = None

# How long the user must have been quiet before a triage pass starts. Shorter
# than `queue.QUIET_SECONDS` because one posting is seconds of the model rather
# than the multi-minute batch a tailoring pass is.
QUIET_SECONDS = 10.0

# How many rows one gate sweep will look at. The gate is free, but the sweep
# runs inside the scheduler tick and a first run over a large mailbox-sized
# backlog should not hold it open.
GATE_BATCH = 2000

STATE_PENDING = 'pending'
STATE_KEPT = 'kept'
STATE_REJECTED = 'rejected'
STATE_ERROR = 'error'


def _now() -> int:
    return int(time.time())


def is_enabled(db) -> bool:
    """Settings → Jobs. Off leaves every row pending, and the feed unchanged."""
    row = db.execute('SELECT job_triage_enabled FROM settings LIMIT 1').fetchone()
    if row is None:
        return True
    return bool(row['job_triage_enabled'])


# --------------------------------------------------------------------------
# The free layer
# --------------------------------------------------------------------------

def run_gate_sweep(db=None) -> dict:
    """Reject what the title alone settles. No model, safe on every tick.

    Runs over `pending` rows only, so a posting a human restored stays
    restored and one the model already judged is never revisited.
    """
    from backend.db.connection import get_db
    from backend.jobs import preferences, profile as profile_mod, triage

    db = db if db is not None else get_db()
    if not is_enabled(db):
        return {'scanned': 0, 'rejected': 0}

    # The application check matches the feed's own exclusion. Without it the
    # filtered list fills with postings the user applied to years ago, which
    # they are conspicuously not missing out on — and the list exists to show
    # what they *are* missing. No description check, though: judging a title is
    # exactly what this layer is for.
    rows = db.execute(
        'SELECT * FROM jobs WHERE triage_state=? AND dismissed=0'
        ' AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)'
        ' LIMIT ?',
        (STATE_PENDING, GATE_BATCH),
    ).fetchall()

    loaded = profile_mod.load_profile(db)
    rejected = 0
    now = _now()
    for row in rows:
        result = triage.gate(row['title'])
        preference_reason = preferences.hard_gate(dict(row), loaded)
        if result.keep and not preference_reason:
            continue
        db.execute(
            'UPDATE jobs SET triage_state=?, triage_reason=?, triage_at=?,'
            ' updated_at=? WHERE id=?',
            (STATE_REJECTED,
             f'profile: {preference_reason}' if preference_reason else f'title: {result.reason}',
             now, now, row['id']),
        )
        rejected += 1
    db.commit()
    return {'scanned': len(rows), 'rejected': rejected}


# --------------------------------------------------------------------------
# The model layer
# --------------------------------------------------------------------------

# The two conditions that make a posting worth a model call, shared by the
# selector and the counter so the backlog figure can never disagree with what
# the worker will actually do.
#
# Both were learned from the live database, where 1,296 of 1,370 pending rows
# were neither:
#
# - **It has a body.** The backfilled rows were reconstructed from confirmation
#   emails, which never contained the posting, so their `description` is empty.
#   Judging one means judging its title — precisely what the cascade exists to
#   avoid, and `triage.py`'s docstring says why.
# - **Nothing has been applied to it.** Those rows have left triage; the feed
#   excludes them for the same reason, so a verdict on one could never be seen.
_TRIAGEABLE = (
    " triage_state='pending' AND dismissed=0 AND triage_error IS NULL"
    " AND length(description) > 0"
    ' AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)'
)


def next_pending(db) -> dict | None:
    """The newest posting still awaiting a verdict, or None.

    **Newest first**, unlike `queue.next_queued`'s oldest-first. A resume queue
    is a queue — the user asked for those in order. This is a backlog, and the
    value of a verdict decays: a posting from last week is likelier to still be
    open than one from two years ago, so a run cut short should have spent
    itself on the recent end. Same reasoning as `refetch.candidates`.

    A row carrying `triage_error` is skipped rather than retried, which is what
    keeps one unparseable posting from blocking everything behind it.
    """
    row = db.execute(
        f'SELECT id FROM jobs WHERE {_TRIAGEABLE} ORDER BY created_at DESC LIMIT 1'
    ).fetchone()
    return {'id': row['id']} if row else None


def pending_count(db) -> int:
    return db.execute(
        f'SELECT COUNT(*) c FROM jobs WHERE {_TRIAGEABLE}'
    ).fetchone()['c']


def _fill_snippet_body(db, job: dict) -> dict:
    """Replace an Adzuna snippet with the real posting, if it can be fetched.

    Adzuna's `description` is a truncated summary, so triaging it would
    summarise a summary and score the fit against ~200 characters. The company
    boards need none of this — Greenhouse, Lever and Ashby all return the full
    body in the listing request.

    A fetch failure is not an error: the snippet is still enough to judge
    relevance, which is the half that matters most.
    """
    from backend.jobs import ingest

    if job.get('source') != 'adzuna' or not job.get('url'):
        return job
    try:
        reasons = json.loads(job.get('match_reasons') or '{}')
    except ValueError:
        reasons = {}
    if not reasons.get('partial'):
        return job

    try:
        text, _title = ingest.fetch_posting(job['url'])
    except Exception as e:
        logger.info('Triage could not fetch %s: %s', job['url'], e)
        return job

    if not text or len(text) <= len(job.get('description') or ''):
        return job

    now = _now()
    db.execute(
        'UPDATE jobs SET description=?, updated_at=? WHERE id=?',
        (text, now, job['id']),
    )
    db.commit()
    return {**job, 'description': text}


def _store(db, job_id: str, verdict: dict) -> None:
    now = _now()
    relevant = bool(verdict.get('relevant'))
    db.execute(
        'UPDATE jobs SET triage_state=?, triage_reason=?, triage_fit=?,'
        ' triage_summary=?, triage_flags=?, triage_at=?, triage_error=NULL,'
        ' updated_at=? WHERE id=?',
        (
            STATE_KEPT if relevant else STATE_REJECTED,
            verdict.get('reason') or '',
            # A fit level on an irrelevant posting is noise: it was computed
            # against a posting that is not going to be shown.
            (verdict.get('fit') or '') if relevant else '',
            verdict.get('summary') or '',
            json.dumps(verdict.get('flags') or []),
            now,
            now,
            job_id,
        ),
    )
    db.commit()


def process_one(job_id: str) -> dict:
    """Judge one posting. Never raises.

    Returns {'ok': bool, 'state': str, 'error': str|None}. Called on the worker
    thread, by the force-run route, and directly by tests.
    """
    from backend.ai import job_triage
    from backend.db.connection import get_db
    from backend.jobs import keywords, preferences, profile as profile_mod, triage

    db = get_db()
    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return {'ok': False, 'state': '', 'error': 'Not found'}
    job = dict(row)

    # The gate again, not only in the sweep: `process_one` is also the route's
    # entry point, and a forced run must not be a way around it.
    gated = triage.gate(job.get('title') or '')
    if not gated.keep:
        now = _now()
        db.execute(
            'UPDATE jobs SET triage_state=?, triage_reason=?, triage_at=?,'
            ' updated_at=? WHERE id=?',
            (STATE_REJECTED, f'title: {gated.reason}', now, now, job_id),
        )
        db.commit()
        return {'ok': True, 'state': STATE_REJECTED, 'error': None}

    loaded = profile_mod.load_profile(db)
    preference_reason = preferences.hard_gate(job, loaded)
    if preference_reason:
        _store(db, job_id, {'relevant': False,
                            'reason': f'profile: {preference_reason}',
                            'fit': '', 'summary': '', 'flags': []})
        return {'ok': True, 'state': STATE_REJECTED, 'error': None}

    try:
        job = _fill_snippet_body(db, job)

        profile_summary = profile_mod.profile_text(loaded)
        facts = triage.posting_facts(
            job.get('title') or '', job.get('description') or ''
        ).to_dict()
        report = keywords.keyword_report(
            job.get('description') or '',
            profile_summary,
            profile_mod.skill_names(loaded),
        ).to_dict()

        # Everything above is DB work; nothing is left open across the call.
        verdict = job_triage.triage_posting(job, profile_summary, facts, report)
        if verdict is not None and verdict.get('relevant'):
            verdict['flags'] = (verdict.get('flags') or []) + preferences.soft_flags(job, loaded)
    except Exception as e:
        logger.warning('Triage failed for %s: %s', job_id, e)
        _record_error(db, job_id, str(e))
        return {'ok': False, 'state': STATE_ERROR, 'error': str(e)}

    if verdict is None:
        # The model is off or unreachable. Leave the row pending so it is
        # picked up when it comes back — recording a verdict nobody reached
        # would be indistinguishable from one that was.
        return {'ok': False, 'state': STATE_PENDING, 'error': 'AI unavailable'}

    _store(db, job_id, verdict)
    return {
        'ok': True,
        'state': STATE_KEPT if verdict.get('relevant') else STATE_REJECTED,
        'error': None,
    }


def _record_error(db, job_id: str, message: str) -> None:
    """Best-effort: losing the error must not lose the worker."""
    try:
        now = _now()
        db.execute(
            'UPDATE jobs SET triage_error=?, triage_at=?, updated_at=? WHERE id=?',
            (message[:500], now, now, job_id),
        )
        db.commit()
    except Exception:
        logger.exception('Could not record triage_error for %s', job_id)


# --------------------------------------------------------------------------
# Worker registry — the shape queue.py uses
# --------------------------------------------------------------------------

def status() -> dict:
    with _lock:
        return {
            'running': _current is not None,
            'current': dict(_current) if _current else None,
            'last': dict(_last) if _last else None,
        }


def _finish(job_id: str, started: float, error: str | None) -> None:
    # Both globals — binding `_current` as a local would leave the worker
    # looking permanently busy and refusing every later posting.
    global _current, _last
    with _lock:
        _last = {
            'jobId': job_id,
            'error': error,
            'seconds': round(time.monotonic() - started, 1),
            'finishedAt': _now(),
        }
        _current = None


def submit(job_id: str) -> bool:
    """Run one posting through the worker. False when already busy."""
    global _current
    with _lock:
        if _current is not None:
            return False
        _current = {'jobId': job_id, 'startedAt': _now()}

    def _run():
        started = time.monotonic()
        error = None
        try:
            error = process_one(job_id).get('error')
        except Exception as e:
            error = str(e)
            logger.warning('Triage worker crashed on %s: %s', job_id, e)
        finally:
            _finish(job_id, started, error)

    _executor.submit(_run)
    return True


def drain_once() -> str | None:
    """Scheduler entry point: submit at most one pending posting.

    Every gate is a question rather than a block, so the tick stays cheap and
    the answers are re-read next time.
    """
    from backend.ai import priority
    from backend.db.connection import get_db

    with _lock:
        if _current is not None:
            return None

    db = get_db()
    if not is_enabled(db):
        return None
    if priority.active() or priority.idle_seconds() < QUIET_SECONDS:
        return None

    pending = next_pending(db)
    if pending is None:
        return None
    return pending['id'] if submit(pending['id']) else None


def restore(db, job_id: str) -> bool:
    """Undo a rejection, by hand.

    Sets the row back to `kept` rather than `pending`, because pending would
    hand it straight back to the layer that just rejected it. The filtered list
    exists so a bad rejection can be found; this is what makes finding one
    useful.
    """
    now = _now()
    cur = db.execute(
        'UPDATE jobs SET triage_state=?, triage_reason=?, triage_error=NULL,'
        ' updated_at=? WHERE id=? AND triage_state IN (?, ?)',
        (STATE_KEPT, 'restored by hand', now, job_id, STATE_REJECTED, STATE_ERROR),
    )
    db.commit()
    return cur.rowcount > 0


def reset_pending(db, job_id: str) -> bool:
    """Send a row back through triage — the explicit retry that clears an error."""
    now = _now()
    cur = db.execute(
        'UPDATE jobs SET triage_state=?, triage_reason=?, triage_fit=?,'
        " triage_summary='', triage_flags=NULL, triage_error=NULL, updated_at=?"
        ' WHERE id=?',
        (STATE_PENDING, '', '', now, job_id),
    )
    db.commit()
    return cur.rowcount > 0


def wait_idle(timeout: float = 30.0) -> bool:
    """Block until no posting is being judged. For tests and shutdown."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            if _current is None:
                return True
        time.sleep(0.01)
    return False


def reset() -> None:
    """Drop registry state. For tests only."""
    global _current, _last
    with _lock:
        _current = None
        _last = None
