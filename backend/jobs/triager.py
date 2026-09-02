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

# How long one tick may keep judging postings back to back.
#
# `drain_once` submits exactly one and returns, which is right for
# `queue.drain_once` — the shape this was written in — because a tailoring pass
# is a multi-minute generation and a handful are queued a day. A triage
# call is 3-8 seconds, and the same shape under a 300-second poll means the
# model works for four seconds and sleeps for 296. That ceiling of twelve
# postings an hour was invisible while a few boards produced tens of postings a
# day, and became a month-long queue the moment the backlog was thousands.
#
# So the drain keeps going while the machine is idle. The gate that makes this
# safe is unchanged and is simply re-read every iteration instead of once every
# five minutes: `drain_once` returns None the moment `priority.active()` is
# true, so a chat message still stands the drain down between generations. What
# changes is only the sleeping.
#
# Bounded rather than unbounded so one tick cannot run for hours while linkage,
# sync and the purge wait behind it. Set to 0 to restore one-per-tick exactly.
DRAIN_BUDGET_SECONDS = 240.0

# Ceiling on waiting for one posting's verdict inside the drain loop. Past this
# the generation is presumed wedged and the tick moves on rather than holding
# the scheduler open — the row stays `pending` and is retried, which is what
# would have happened anyway.
DRAIN_STEP_TIMEOUT = 120.0

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
    """Reject what the title and the profile settle. No model, safe on every tick.

    Runs over `pending` rows only, so a posting a human restored stays
    restored and one the model already judged is never revisited.

    **Pages to the end rather than stopping after one batch.** A rejected row
    leaves `pending` and so leaves the result set, but a *kept* one stays in it
    forever — so a plain `LIMIT` re-reads the same leading rows every tick and
    only creeps forward by however many it just rejected. Once the first
    `GATE_BATCH` pending rows are all keeps it advances by nothing at all, and
    every posting behind them is reachable only by the model drain, one 3-8
    second generation at a time. That is how 3,758 rows ended up queued behind
    a gate that had already decided about them. `offset` therefore counts the
    rows this sweep *kept*, which is exactly the number the next page has to
    step over.

    The batch is now only how much is held in memory at once. There is no
    reason to rate-limit the work itself: it is string comparison and a
    gazetteer lookup, no model and no network, so the whole table costs a
    couple of seconds.
    """
    from backend.db.connection import get_db
    from backend.jobs import preferences, profile as profile_mod, triage

    db = db if db is not None else get_db()
    if not is_enabled(db):
        return {'scanned': 0, 'rejected': 0}

    loaded = profile_mod.load_profile(db)
    scanned = 0
    rejected = 0
    offset = 0
    now = _now()
    while True:
        # The application check matches the feed's own exclusion. Without it
        # the filtered list fills with postings the user applied to years ago,
        # which they are conspicuously not missing out on — and the list exists
        # to show what they *are* missing. No description check, though:
        # judging a title is exactly what this layer is for.
        rows = db.execute(
            'SELECT * FROM jobs WHERE triage_state=? AND dismissed=0'
            ' AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)'
            ' ORDER BY rowid LIMIT ? OFFSET ?',
            (STATE_PENDING, GATE_BATCH, offset),
        ).fetchall()
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            result = triage.gate(row['title'])
            preference_reason = preferences.hard_gate(dict(row), loaded)
            if result.keep and not preference_reason:
                offset += 1
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
    return {'scanned': scanned, 'rejected': rejected}


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
        ' triage_summary=?, triage_flags=?, work_location=?, triage_at=?,'
        ' triage_error=NULL, updated_at=? WHERE id=?',
        (
            STATE_KEPT if relevant else STATE_REJECTED,
            verdict.get('reason') or '',
            # A fit level on an irrelevant posting is noise: it was computed
            # against a posting that is not going to be shown.
            (verdict.get('fit') or '') if relevant else '',
            verdict.get('summary') or '',
            json.dumps(verdict.get('flags') or []),
            verdict.get('workLocation') or '',
            now,
            now,
            job_id,
        ),
    )
    _store_inferred_distance(db, job_id, verdict)
    db.commit()


def _store_inferred_distance(db, job_id: str, verdict: dict) -> None:
    """Fill `distance_km` from the model's cities, but only as a last resort.

    **A structured reading is never overwritten.** The board's own location
    field and a posted coordinate are both statements by the employer; this
    came from a model reading prose, and letting it replace either would swap a
    fact for an inference on the column the feed sorts by. So the update is
    guarded on `distance_km IS NULL` in SQL rather than in Python — a re-triage
    racing a re-sync must not be able to win.

    The complementary case is why it exists at all: a posting whose location
    field says only "Remote - Canada" while the body asks for two days a week
    in a Toronto office has no structured reading to protect.
    """
    from backend.jobs import distance

    reading = distance.resolve_keys(verdict.get('cities'))
    if reading is None:
        return
    db.execute(
        'UPDATE jobs SET distance_km=?, distance_precision=?'
        ' WHERE id=? AND distance_km IS NULL',
        (reading.km, reading.precision, job_id),
    )


def process_one(job_id: str) -> dict:
    """Judge one posting. Never raises.

    Returns {'ok': bool, 'state': str, 'error': str|None}. Called on the worker
    thread, by the force-run route, and directly by tests.
    """
    from backend.ai import job_triage
    from backend.db.connection import get_db
    from backend.jobs import distance, keywords, preferences, profile as profile_mod, triage

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
        # The gazetteer's own keys go in as the enum bound, so the model can
        # only point at a place `distance.py` can already measure.
        verdict = job_triage.triage_posting(
            job, profile_summary, facts, report, distance.selectable_places()
        )
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


def drain_while_idle(budget_seconds: float | None = None) -> dict:
    """Judge postings back to back for as long as the machine stays idle.

    Every iteration goes through `drain_once`, so all four of its gates —
    another pass already running, triage switched off, `priority.active()`, and
    an empty queue — are re-read between every generation. None of the deferral
    behaviour changes; the loop only removes the five-minute sleep between two
    four-second calls.

    Each submission is waited on before the next is considered. Without that
    the executor would simply accept a queue of jobs and the priority check
    between them would never happen — which is the whole point of looping here
    rather than raising a batch size.

    Returns what happened, so the scheduler's result dict can say it.
    """
    budget = DRAIN_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = time.monotonic() + max(0.0, budget)

    first = drain_once()
    if first is None:
        return {'submitted': 0, 'stopped': 'idle'}

    # Every posting this pass has already handed to the worker. A row that
    # comes back a second time means the last generation did not move it out
    # of `pending`, and `process_one` does exactly that on purpose when the
    # model is unreachable — it declines to record a verdict nobody reached.
    # One posting per five minutes made that invisible; a loop turns it into
    # thousands of retries against a dead llama-server inside one tick. So the
    # loop stops on the absence of forward progress rather than trying to
    # enumerate the reasons for it.
    seen = {first}
    submitted = 1
    stopped = 'budget'
    while True:
        if not wait_idle(timeout=DRAIN_STEP_TIMEOUT):
            stopped = 'timeout'
            break
        if time.monotonic() >= deadline:
            break

        # Peek before submitting rather than after. Checking the id `drain_once`
        # returns would work too, but only once the repeat had already been
        # handed to the worker — so the pass would both waste a generation and
        # return with one still in flight.
        if _repeats(seen):
            stopped = 'stalled'
            break

        job_id = drain_once()
        if job_id is None:
            # Nothing left, the user came back, or triage was switched off.
            stopped = 'idle'
            break
        seen.add(job_id)
        submitted += 1

    return {'submitted': submitted, 'stopped': stopped}


def _repeats(seen: set[str]) -> bool:
    """True when the next candidate is one this pass already judged."""
    from backend.db.connection import get_db

    try:
        nxt = next_pending(get_db())
    except Exception as e:
        logger.warning('Could not look ahead in the triage queue: %s', e)
        return True
    return nxt is not None and nxt['id'] in seen


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
