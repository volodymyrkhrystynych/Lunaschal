"""Importing and refreshing a registered repository.

Both jobs run on the research worker (backend/research/worker.py), never on
`run_bg`: a first clone of a large repo is minutes of network, and run_bg's
single FIFO queue is shared with journal polish, attachment transcription and
five other flows the user triggered seconds earlier.

Order is the same in both, and it is the order that matters: **get the tree,
then the graph, then record what we have.** A failed graph build must never cost
the checkout, so it is written as a separate, nullable field — the same shape as
repo_job.py's change_summary sitting behind the facts it must not be able to
lose.
"""
import logging

from backend.repos import graph, registry, storage
from backend.repos.git import GitError, UnsafeRemote, clone, current_branch, head_sha, pull

logger = logging.getLogger(__name__)


def run_import(repo_id: str, cancel=None) -> dict:
    """Clone a pending repo and build its graph. Returns a small summary."""
    repo = registry.get_repo(repo_id)
    if not repo:
        return {'ok': False, 'error': 'no such repo'}

    dest = storage.repo_dir(repo['slug'])
    if dest is None:
        registry.set_state(repo_id, 'error', 'Unusable repository slug')
        return {'ok': False, 'error': 'bad slug'}

    registry.set_state(repo_id, 'cloning')
    # A retry of a failed import finds the previous half-clone in the way.
    if dest.exists():
        storage.delete_repo_dir(repo['slug'])

    try:
        clone(repo['remoteUrl'], dest, repo.get('branch') or '')
    except (GitError, UnsafeRemote) as e:
        registry.set_state(repo_id, 'error', str(e))
        logger.warning('Clone failed for %s: %s', repo['slug'], e)
        return {'ok': False, 'error': str(e)}

    return _finish_sync(repo_id, dest, cancel=cancel)


def run_pull(repo_id: str, cancel=None) -> dict:
    """Fetch, hard-reset and refresh the graph for an already-cloned repo."""
    repo = registry.get_repo(repo_id)
    if not repo:
        return {'ok': False, 'error': 'no such repo'}
    root = storage.repo_dir(repo['slug'])
    if root is None or not (root / '.git').exists():
        # The directory went missing under us — a full re-import is the fix,
        # and saying so beats a confusing git error.
        registry.set_state(repo_id, 'error', 'Checkout is missing; re-import the repo')
        return {'ok': False, 'error': 'missing checkout'}

    try:
        pull(root, repo.get('branch') or '')
    except GitError as e:
        registry.set_state(repo_id, 'error', str(e))
        logger.warning('Pull failed for %s: %s', repo['slug'], e)
        return {'ok': False, 'error': str(e)}

    return _finish_sync(repo_id, root, cancel=cancel)


def _finish_sync(repo_id: str, root, cancel=None) -> dict:
    """Graph, then record. Shared by import and pull so the two cannot drift."""
    if cancel is not None and cancel.is_set():
        # Cancelled after the tree landed: the checkout is good, so mark it
        # ready and leave the graph for the next pull rather than reporting a
        # failure over work that succeeded.
        registry.set_state(repo_id, 'ready')
        return {'ok': True, 'cancelled': True}

    built = graph.build(root)

    try:
        sha, branch = head_sha(root), current_branch(root)
    except GitError as e:
        sha, branch = None, None
        logger.warning('Could not read HEAD for %s: %s', root, e)

    registry.record_sync(
        repo_id,
        head_sha=sha,
        branch=branch if branch and branch != 'HEAD' else None,
        graph_node_count=(built or {}).get('nodeCount'),
    )
    registry.set_state(repo_id, 'ready')
    return {
        'ok': True,
        'headSha': sha,
        'graphNodes': (built or {}).get('nodeCount'),
    }


def submit_import(repo_id: str) -> bool:
    from backend.research import worker
    return worker.submit('repo-import', lambda cancel: run_import(repo_id, cancel), repo_id)


def submit_pull(repo_id: str) -> bool:
    from backend.research import worker
    return worker.submit('repo-pull', lambda cancel: run_pull(repo_id, cancel), repo_id)
