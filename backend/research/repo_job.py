"""The repo-context snapshot run.

Kept separate from the scheduler (backend/research/repo_scheduler.py) so tests
and the Settings "Refresh now" button can call it directly, the same split the
overnight briefing uses.
"""
import json
import logging
import time

from ulid import ULID

from backend.ai.repo_context import render_change_summary, summarize_delta
from backend.db.connection import get_db
from backend.research import repo_facts

logger = logging.getLogger(__name__)

# Snapshots are small (tens of KB of JSON) but unbounded in time; two weeks of
# history is plenty to answer "what changed recently".
KEEP_SNAPSHOTS = 30


def current_snapshot() -> dict | None:
    """The newest snapshot as a row dict, or None before the first run."""
    from backend.db.connection import row_to_dict
    row = get_db().execute(
        'SELECT * FROM repo_snapshots ORDER BY generated_at DESC, id DESC LIMIT 1'
    ).fetchone()
    return row_to_dict(row) if row else None


def _latest_row():
    return get_db().execute(
        'SELECT id, git_sha FROM repo_snapshots ORDER BY generated_at DESC, id DESC LIMIT 1'
    ).fetchone()


def run_repo_snapshot(now: int | None = None, force: bool = False) -> dict | None:
    """Build a snapshot of the repo. Returns the new row's summary, or None
    when the repo has not moved since the last one (unless `force`).

    Ordering matters: the facts are extracted and committed before the model is
    ever called, so an LLM failure — or a restart mid-call — cannot cost us the
    deterministic half.
    """
    root = repo_facts.repo_root()
    if not repo_facts.is_repo(root):
        logger.warning('Repo-context skipped: %s is not a Lunaschal checkout', root)
        return None

    db = get_db()
    previous = _latest_row()
    prev_sha = previous['git_sha'] if previous else None

    facts = repo_facts.build_facts(root, db, since_sha=prev_sha)
    sha = (facts.get('git') or {}).get('sha')

    if not force and sha and prev_sha == sha:
        return None

    now = now or int(time.time())
    snapshot_id = str(ULID())
    warnings = (facts.get('views') or {}).get('warnings') or []

    db.execute(
        'INSERT INTO repo_snapshots(id, git_sha, git_branch, facts, digest,'
        ' change_summary, route_count, table_count, component_count, warnings,'
        ' prev_snapshot_id, generated_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (
            snapshot_id,
            sha,
            (facts.get('git') or {}).get('branch'),
            repo_facts.facts_json(facts),
            repo_facts.render_digest(facts),
            None,
            len(facts.get('routes') or []),
            len([t for t in facts.get('tables') or [] if not t.get('virtual')]),
            len(facts.get('components') or []),
            json.dumps(warnings) if warnings else None,
            previous['id'] if previous else None,
            now,
            now,
        ),
    )
    db.commit()

    # Only now, with the facts safely on disk, spend a model call on the prose.
    # Never hold a transaction across an LLM call: get_db() hands out one
    # process-global connection, so any request handler's commit() would
    # commit whatever this thread had pending.
    summary_md = None
    try:
        summary = summarize_delta(
            (facts.get('git') or {}).get('commits') or [],
            (facts.get('git') or {}).get('diffstat') or '',
        )
        summary_md = render_change_summary(summary)
    except Exception as e:
        logger.warning('Repo-context summary failed: %s', e)

    if summary_md:
        db.execute(
            'UPDATE repo_snapshots SET change_summary=? WHERE id=?',
            (summary_md, snapshot_id),
        )
        db.commit()

    _prune(db)

    return {
        'id': snapshot_id,
        'gitSha': sha,
        'routeCount': len(facts.get('routes') or []),
        'tableCount': len([t for t in facts.get('tables') or [] if not t.get('virtual')]),
        'warnings': warnings,
        'changeSummary': summary_md,
    }


def _prune(db) -> None:
    db.execute(
        'DELETE FROM repo_snapshots WHERE id NOT IN ('
        ' SELECT id FROM repo_snapshots ORDER BY generated_at DESC, id DESC LIMIT ?)',
        (KEEP_SNAPSHOTS,),
    )
    db.commit()
