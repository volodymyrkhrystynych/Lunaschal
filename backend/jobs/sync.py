"""Pulling saved searches into `jobs`, and scoring what lands.

Two rules govern the upsert, and both are the kind that are cheap now and
expensive to discover in three weeks:

- **A re-sync must never clear `dismissed`.** Boards re-list the same posting
  every night. If a sync overwrote the row wholesale, every job the user
  rejected would be back in the feed by morning, and a feed you have to reject
  the same posting in twice is a feed nobody opens again.
- **A re-sync must not touch `created_at`.** It is what "new since yesterday"
  is measured from.

So volatile fields (title, salary, description, `fetched_at`) are refreshed and
judgement fields (`dismissed`, `created_at`) are left alone.

Scoring is deterministic and runs inline. `keyword_report` is pure string work
over a vocabulary, so scoring two hundred postings costs nothing and needs no
model, no window and no `priority` gate — which is what lets the feed be sorted
the moment a sync finishes rather than hours later.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.jobs import distance, keywords, profile as profile_mod
from backend.jobs.sources import SourceError, fetch as fetch_source

logger = logging.getLogger(__name__)

# Politeness between two board calls in one sweep. These are documented APIs
# rather than scraped pages, but the house rule is serial and unhurried, and a
# sweep that takes twenty seconds instead of two costs nothing here.
INTER_REQUEST_DELAY = 1.0

DEFAULT_INTERVAL_HOURS = 24


def matches_hunt(job: dict, params: dict) -> bool:
    """Apply source-independent saved-hunt filters before a posting is stored."""
    title = (job.get('title') or '').lower()
    location = (job.get('location') or '').lower()
    seniority = (params.get('seniority') or '').strip().lower()
    title_terms = params.get('titleTerms') or []
    if isinstance(title_terms, str):
        title_terms = [title_terms]
    if title_terms and not any(str(term).lower() in title for term in title_terms if term):
        return False
    wanted_location = (params.get('locationFilter') or '').strip().lower()
    if wanted_location and wanted_location not in location:
        return False
    if params.get('remoteOnly') and not job.get('remote'):
        return False
    if seniority and seniority not in title:
        return False
    try:
        floor = float(params.get('salaryFloor')) if params.get('salaryFloor') not in (None, '') else None
    except (TypeError, ValueError):
        floor = None
    salary_max = job.get('salaryMax')
    if floor is not None and (salary_max is None or float(salary_max) < floor):
        return False
    return True


def _now() -> int:
    return int(time.time())


def parse_posted_at(value) -> int | None:
    """Board timestamps, in the three shapes they actually arrive in.

    Epoch milliseconds (Lever), ISO-8601 with or without a 'Z' (Greenhouse,
    Ashby, Adzuna), or nothing at all. An unparseable date becomes None rather
    than now() — a wrong posting date silently re-sorts the feed.
    """
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Lever sends milliseconds; anything past this is not seconds.
        return int(value / 1000) if value > 1e11 else int(value)
    try:
        text = str(value).strip().replace('Z', '+00:00')
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (ValueError, TypeError):
        return None


def score_job(job: dict, loaded_profile: dict) -> tuple[float | None, str | None]:
    """(match_score, match_reasons JSON) for one posting.

    Returns (None, None) when there is no profile to match against — a score of
    zero would be a claim, and "not scored yet" is the honest state.
    """
    if profile_mod.is_empty(loaded_profile):
        return None, None

    report = keywords.keyword_report(
        job.get('description') or '',
        profile_mod.profile_text(loaded_profile),
        profile_mod.skill_names(loaded_profile),
    )
    reasons = report.to_dict()
    if job.get('descriptionIsSnippet'):
        # Adzuna scores against a snippet. Flagged so the UI can say the number
        # is provisional rather than presenting it as the same measurement the
        # company boards get.
        reasons['partial'] = True
    return report.coverage, json.dumps(reasons)


def upsert_job(db, kind: str, job: dict, loaded_profile: dict) -> str | None:
    """Insert or refresh one posting. Returns its id, or None if unusable."""
    source_id = (job.get('sourceId') or '').strip()
    if not source_id or not (job.get('title') or '').strip():
        return None

    score, reasons = score_job(job, loaded_profile)
    reading = distance.reading_for(job)
    now = _now()
    existing = db.execute(
        'SELECT id, description FROM jobs WHERE source=? AND source_id=?',
        (kind, source_id),
    ).fetchone()

    fields = {
        'url': job.get('url') or '',
        'company': job.get('company') or '',
        'title': job.get('title') or '',
        'location': job.get('location') or '',
        'remote': 1 if job.get('remote') else 0,
        'salary_min': job.get('salaryMin'),
        'salary_max': job.get('salaryMax'),
        'salary_currency': job.get('salaryCurrency') or '',
        'description': job.get('description') or '',
        'raw': json.dumps(job.get('raw')) if job.get('raw') is not None else None,
        'match_score': score,
        'match_reasons': reasons,
        # Deterministic, like the score above, and refreshed on every list for
        # the same reason: a board that moves a posting from Toronto to Ottawa
        # has changed the only thing this column measures.
        'distance_km': reading.km if reading else None,
        'distance_precision': reading.precision if reading else '',
        'posted_at': parse_posted_at(job.get('postedAt')),
        'fetched_at': now,
        'updated_at': now,
    }

    if existing:
        # Note what is absent: `dismissed` and `created_at`. See the module
        # docstring — refreshing those is how a feed loses the user's trust.
        assignments = ', '.join(f'{col}=?' for col in fields)
        db.execute(
            f'UPDATE jobs SET {assignments} WHERE id=?',
            (*fields.values(), existing['id']),
        )
        # A third judgement field, for the same reason as the other two: boards
        # re-list the same posting nightly with byte-identical text, and
        # re-triaging on every list would spend the model on ~1,300 verdicts a
        # night to reproduce yesterday's. Only a body that actually changed is
        # worth a second opinion — but that one is, or a rewritten posting
        # keeps a summary describing a job it no longer is.
        if (existing['description'] or '') != fields['description']:
            db.execute(
                "UPDATE jobs SET triage_state='pending', triage_reason='',"
                " triage_fit='', triage_summary='', triage_flags=NULL,"
                ' triage_error=NULL WHERE id=?',
                (existing['id'],),
            )
        return existing['id']

    job_id = str(ULID())
    columns = ['id', 'source', 'source_id', *fields.keys(), 'created_at']
    values = [job_id, kind, source_id, *fields.values(), now]
    placeholders = ', '.join('?' * len(columns))
    db.execute(
        f'INSERT INTO jobs ({", ".join(columns)}) VALUES ({placeholders})', values
    )
    return job_id


def adzuna_credentials(db) -> dict:
    row = db.execute(
        'SELECT adzuna_app_id, adzuna_app_key FROM settings LIMIT 1'
    ).fetchone()
    if not row:
        return {}
    return {'app_id': row['adzuna_app_id'] or '', 'app_key': row['adzuna_app_key'] or ''}


def sync_search(db, search: dict) -> dict:
    """Run one saved search and fold the result into `jobs`.

    Always stamps `last_run_at`, including on failure — otherwise a search that
    errors every time is re-attempted on every single tick.
    """
    kind = search['kind']
    params = search.get('params') or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = {}

    result = {'searchId': search['id'], 'kind': kind, 'added': 0,
              'updated': 0, 'error': None, 'message': ''}
    now = _now()

    try:
        creds = adzuna_credentials(db) if kind == 'adzuna' else {}
        fetched = fetch_source(kind, params, creds=creds)
    except SourceError as e:
        result['error'] = str(e)
        db.execute(
            'UPDATE job_searches SET last_run_at=?, last_error=?, updated_at=? WHERE id=?',
            (now, str(e), now, search['id']),
        )
        db.commit()
        return result

    loaded_profile = profile_mod.load_profile(db)
    seen_before = {
        row['source_id']
        for row in db.execute('SELECT source_id FROM jobs WHERE source=?', (kind,))
    }

    for job in fetched.jobs:
        if not matches_hunt(job, params):
            continue
        job_id = upsert_job(db, kind, job, loaded_profile)
        if job_id is None:
            continue
        if (job.get('sourceId') or '') in seen_before:
            result['updated'] += 1
        else:
            result['added'] += 1

    result['message'] = fetched.message
    db.execute(
        'UPDATE job_searches SET last_run_at=?, last_count=?, last_error=NULL, '
        'updated_at=? WHERE id=?',
        (now, len(fetched.jobs), now, search['id']),
    )
    db.commit()
    return result


def due_searches(db, now: int | None = None) -> list[dict]:
    """Enabled searches whose interval has elapsed, oldest run first."""
    now = now if now is not None else _now()
    rows = db.execute(
        'SELECT * FROM job_searches WHERE enabled=1 '
        'ORDER BY last_run_at IS NOT NULL, last_run_at'
    ).fetchall()

    due = []
    for row in rows:
        search = row_to_dict(row)
        # row_to_dict turns last_run_at into an ISO string, so compare on raw.
        last_run = row['last_run_at']
        interval = (row['interval_hours'] or DEFAULT_INTERVAL_HOURS) * 3600
        if last_run is None or now - last_run >= interval:
            search['params'] = row['params']
            due.append(search)
    return due


def run_sync_sweep(now: int | None = None) -> dict:
    """Every due search, one at a time. Safe to call from the scheduler tick."""
    db = get_db()
    searches = due_searches(db, now)
    results = []
    for index, search in enumerate(searches):
        if index:
            time.sleep(INTER_REQUEST_DELAY)
        try:
            results.append(sync_search(db, search))
        except Exception as e:
            # One malformed board response must not stop the other searches.
            logger.warning('Job search %s failed: %s', search.get('id'), e)
            results.append({'searchId': search.get('id'), 'error': str(e)})
    return {
        'searches': len(searches),
        'added': sum(r.get('added') or 0 for r in results),
        'updated': sum(r.get('updated') or 0 for r in results),
        'results': results,
    }


def rescore_all(db) -> int:
    """Recompute every undismissed job's score against the current profile.

    The profile is edited far more often than postings arrive, and a score
    computed against last month's skills list is worse than no score. Called
    after a profile edit; cheap enough to run synchronously.
    """
    loaded = profile_mod.load_profile(db)
    rows = db.execute(
        'SELECT id, description, match_reasons FROM jobs WHERE dismissed=0'
    ).fetchall()

    changed = 0
    for row in rows:
        was_partial = False
        if row['match_reasons']:
            try:
                was_partial = bool(json.loads(row['match_reasons']).get('partial'))
            except ValueError:
                pass
        score, reasons = score_job(
            {'description': row['description'], 'descriptionIsSnippet': was_partial},
            loaded,
        )
        db.execute(
            'UPDATE jobs SET match_score=?, match_reasons=? WHERE id=?',
            (score, reasons, row['id']),
        )
        changed += 1
    db.commit()
    return changed


def recompute_distances(db) -> int:
    """Recompute `distance_km` for every undismissed posting.

    Separate from `rescore_all` because it answers a different question and
    depends on different inputs: the score is a function of the *profile*, this
    is a function of the *posting*. Folding it into the profile-edit path would
    tie a number that never changes with the profile to a trigger that fires
    every time a skill is added.

    Coordinates come from `raw` when the adapter carried them — Adzuna is the
    only one that does — so a re-run picks up rows synced before this column
    existed without re-fetching anything from the board.
    """
    rows = db.execute(
        'SELECT id, location, raw FROM jobs WHERE dismissed=0'
    ).fetchall()

    changed = 0
    for row in rows:
        job = {'location': row['location'] or ''}
        if row['raw']:
            try:
                raw = json.loads(row['raw'])
            except ValueError:
                raw = None
            if isinstance(raw, dict):
                job['latitude'] = raw.get('latitude')
                job['longitude'] = raw.get('longitude')
        reading = distance.reading_for(job)
        db.execute(
            'UPDATE jobs SET distance_km=?, distance_precision=? WHERE id=?',
            (reading.km if reading else None,
             reading.precision if reading else '', row['id']),
        )
        changed += 1
    db.commit()
    return changed
