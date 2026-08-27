"""Nightly repo-context daemon.

The fourth of the app's hand-rolled scheduler loops (see
backend/briefing_scheduler.py, which this copies). The window defaults to
03:00-05:00: the chat-title sweep owns 02:00-03:00 and the briefing owns
05:00-07:00, so this contends with neither for the two llama slots — and it
runs *before* the briefing, so a morning briefing sees a current snapshot.

Each night it does, per registered repository and then for this app's own
checkout: **pull, graph, scan, write.**

- `git fetch` + `reset --hard` — the tree is the truth, and reset leaves the
  untracked graph alone.
- `graphify update --force` — 0.2 s on a small repo, no LLM, no API key. Forced
  past the "fewer nodes" guard because the tree was just reset to the remote, so
  a commit that deletes a package *should* shrink the graph rather than leave a
  stale one standing.
- the deterministic snapshot.
- a few code-wiki module notes.

Only the last of those spends model time, and it goes through the priority gate
between modules, so a pass still running at 05:00 yields to whatever the user
asks for rather than fighting the briefing for a slot.

The run bodies live in repo_job.py and code_wiki.py so tests and the Settings
buttons can call them directly, the same split the overnight briefing uses.
"""
import logging
import os
import threading
import time
from datetime import datetime

from backend.db.connection import get_db
from backend.research.repo_job import run_repo_snapshot

logger = logging.getLogger(__name__)

WINDOW_SPAN_HOURS = 2
_POLL_SECONDS = 300

DEFAULT_ENABLED = True
DEFAULT_HOUR = 3


def repo_context_settings() -> tuple[bool, int]:
    """(enabled, hour) from settings, with defaults when unset."""
    try:
        row = get_db().execute(
            'SELECT repo_context_enabled, repo_context_hour FROM settings LIMIT 1'
        ).fetchone()
    except Exception:
        return DEFAULT_ENABLED, DEFAULT_HOUR
    if not row:
        return DEFAULT_ENABLED, DEFAULT_HOUR
    enabled = bool(
        row['repo_context_enabled'] if row['repo_context_enabled'] is not None else 1
    )
    hour = row['repo_context_hour'] if row['repo_context_hour'] is not None else DEFAULT_HOUR
    return enabled, hour


def in_window(hour: int, now: datetime) -> bool:
    """True inside [hour, hour + WINDOW_SPAN_HOURS)."""
    return hour <= now.hour < hour + WINDOW_SPAN_HOURS


def should_run(enabled: bool, hour: int, now: datetime, last_run_date) -> bool:
    """The whole scheduling decision, pulled out so it is testable without
    starting a thread that has no stop signal."""
    return enabled and in_window(hour, now) and last_run_date != now.date()


def run_nightly(cancel=None) -> dict:
    """One night's work across every repository. Returns a per-repo summary.

    Sequential on purpose: two repos pulling and scanning at once would give
    the disk and the model nothing but contention, and there is all night.
    """
    from backend.repos import job, registry
    from backend.research import code_wiki

    results = {}
    for repo in registry.list_repos():
        if cancel is not None and cancel.is_set():
            break
        try:
            results[repo['slug']] = _run_one(repo, job, code_wiki, cancel)
        except Exception as e:
            # One repo's failure must not cost the others their night.
            logger.warning('Nightly pass failed for %s: %s', repo['slug'], e)
            results[repo['slug']] = {'error': str(e)}

    # This app's own checkout is not a registered repo and has no clone to
    # pull; it still gets the snapshot it always got.
    try:
        run_repo_snapshot()
    except Exception as e:
        logger.warning('Self repo-context snapshot failed: %s', e)
    return results


def _run_one(repo: dict, job, code_wiki, cancel) -> dict:
    """Pull, graph, scan, write — for one repository, in that order.

    The order is the point: a snapshot of a tree that was not pulled describes
    yesterday, and a code note written against it cites lines that moved.
    """
    if repo.get('cloneState') == 'ready':
        pulled = job.run_pull(repo['id'], cancel)
    else:
        # A repo that never finished cloning gets its clone now rather than
        # being skipped forever.
        pulled = job.run_import(repo['id'], cancel)
    if not pulled.get('ok'):
        return {'pulled': False, 'error': pulled.get('error')}

    snapshot = run_repo_snapshot(repo_id=repo['id'])
    written = code_wiki.run_code_wiki(repo['id'], cancel=cancel)
    return {
        'pulled': True,
        'snapshot': bool(snapshot),
        'articles': written.get('written') or [],
        'skipped': written.get('skipped', 0),
    }


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            enabled, hour = repo_context_settings()
            now = datetime.now()
            if should_run(enabled, hour, now, last_run_date):
                last_run_date = now.date()
                run_nightly()
        except Exception as e:
            logger.warning('Repo-context nightly pass failed: %s', e)
        time.sleep(_POLL_SECONDS)


def start_repo_context_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
