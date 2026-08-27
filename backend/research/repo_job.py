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


def current_snapshot(repo_id: str | None = None) -> dict | None:
    """The newest snapshot for a repo, or None before its first scan.

    Ordered by `generated_at DESC, id DESC`: generated_at is second-resolution,
    so two scans in the same second tie and the ULID is what actually breaks it.
    Without the tiebreak, scanning twice could leave the app reading the *older*
    snapshot — a real bug, caught by a test.
    """
    from backend.db.connection import row_to_dict
    clause, params = _repo_clause(repo_id)
    row = get_db().execute(
        f'SELECT * FROM repo_snapshots WHERE {clause}'
        ' ORDER BY generated_at DESC, id DESC LIMIT 1',
        params,
    ).fetchone()
    return row_to_dict(row) if row else None


def _repo_clause(repo_id: str | None) -> tuple[str, list]:
    """`repo_id = NULL` is never true in SQL, so the unscoped case needs IS."""
    if repo_id is None:
        return 'repo_id IS NULL', []
    return 'repo_id = ?', [repo_id]


def _latest_row(repo_id: str | None = None):
    clause, params = _repo_clause(repo_id)
    return get_db().execute(
        f'SELECT id, git_sha FROM repo_snapshots WHERE {clause}'
        ' ORDER BY generated_at DESC, id DESC LIMIT 1',
        params,
    ).fetchone()


def run_repo_snapshot(
    now: int | None = None,
    force: bool = False,
    repo_id: str | None = None,
) -> dict | None:
    """Build a snapshot of a repo. Returns the new row's summary, or None when
    the repo has not moved since the last one (unless `force`).

    With no `repo_id` this scans the checkout the app is running from, which is
    what it always did. With one, it scans that registered repository's clone.

    Ordering matters: the facts are extracted and committed before the model is
    ever called, so an LLM failure — or a restart mid-call — cannot cost us the
    deterministic half.
    """
    root = _root_for(repo_id)
    if root is None:
        logger.warning('Repo-context skipped: no usable checkout for %s', repo_id)
        return None

    db = get_db()
    previous = _latest_row(repo_id)
    prev_sha = previous['git_sha'] if previous else None

    facts = repo_facts.build_facts(root, db, since_sha=prev_sha)
    sha = (facts.get('git') or {}).get('sha')

    if not force and sha and prev_sha == sha:
        return None

    now = now or int(time.time())
    snapshot_id = str(ULID())
    warnings = (facts.get('views') or {}).get('warnings') or []

    db.execute(
        'INSERT INTO repo_snapshots(id, repo_id, git_sha, git_branch, facts, digest,'
        ' change_summary, route_count, table_count, component_count, warnings,'
        ' prev_snapshot_id, generated_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (
            snapshot_id,
            repo_id,
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

    _prune(db, repo_id)

    return {
        'id': snapshot_id,
        'gitSha': sha,
        'routeCount': len(facts.get('routes') or []),
        'tableCount': len([t for t in facts.get('tables') or [] if not t.get('virtual')]),
        'warnings': warnings,
        'changeSummary': summary_md,
    }


def _root_for(repo_id: str | None):
    """The checkout to scan.

    No repo_id means the checkout this app runs from — the original behaviour,
    and still what the Settings "Scan now" button does. A repo_id means a
    registered clone, and `is_repo`'s Lunaschal fingerprint deliberately does
    not apply there: an arbitrary repository is still worth scanning, it just
    yields the generic half of the facts.
    """
    if repo_id is None:
        root = repo_facts.repo_root()
        return root if repo_facts.is_repo(root) else None
    from backend.repos import registry
    root = registry.repo_root(repo_id)
    return root if root and (root / '.git').exists() else None


def _prune(db, repo_id: str | None = None) -> None:
    """Keep the newest KEEP_SNAPSHOTS *per repo*.

    Pruning globally would let a busy repo's history evict a quiet one's only
    snapshot, and an idea judged against nothing gets no assessment at all.
    """
    clause, params = _repo_clause(repo_id)
    db.execute(
        f'DELETE FROM repo_snapshots WHERE {clause} AND id NOT IN ('
        f' SELECT id FROM repo_snapshots WHERE {clause}'
        ' ORDER BY generated_at DESC, id DESC LIMIT ?)',
        [*params, *params, KEEP_SNAPSHOTS],
    )
    db.commit()
