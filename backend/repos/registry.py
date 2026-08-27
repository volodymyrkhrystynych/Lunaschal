"""The `repos` table: registering, listing and forgetting a repository.

Deliberately thin. Everything that touches the network or the filesystem lives
in git.py/graph.py/job.py; this is the row layer, so the route handlers and the
worker read the same statements rather than each writing their own.
"""
import logging
import time

from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.repos import storage
from backend.repos.git import assert_clone_url, slug_for_url

logger = logging.getLogger(__name__)


def list_repos() -> list[dict]:
    rows = get_db().execute(
        'SELECT * FROM repos ORDER BY is_default DESC, name COLLATE NOCASE'
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_repo(repo_id: str) -> dict | None:
    row = get_db().execute('SELECT * FROM repos WHERE id=?', (repo_id,)).fetchone()
    return row_to_dict(row) if row else None


def get_by_slug(slug: str) -> dict | None:
    row = get_db().execute('SELECT * FROM repos WHERE slug=?', (slug,)).fetchone()
    return row_to_dict(row) if row else None


def default_repo() -> dict | None:
    """The repo an idea belongs to when nothing says otherwise.

    The flagged default, or — because a single-repo setup should need no
    configuring at all — the only ready one.
    """
    db = get_db()
    row = db.execute('SELECT * FROM repos WHERE is_default=1 LIMIT 1').fetchone()
    if row:
        return row_to_dict(row)
    rows = db.execute("SELECT * FROM repos WHERE clone_state='ready'").fetchall()
    return row_to_dict(rows[0]) if len(rows) == 1 else None


def repo_root(repo_id: str | None):
    """The clone directory for a repo, or None if it has no usable checkout."""
    if not repo_id:
        return None
    repo = get_repo(repo_id)
    if not repo or repo.get('cloneState') != 'ready':
        return None
    return storage.repo_dir(repo['slug'])


def _unique_slug(db, base: str) -> str:
    taken = {r['slug'] for r in db.execute('SELECT slug FROM repos').fetchall()}
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f'{base}-{n}'
        if candidate not in taken:
            return candidate
    return f'{base}-{int(time.time())}'


def create_repo(url: str, name: str = '', branch: str = '') -> dict:
    """Register a repo in the 'pending' state. Cloning is the job's business.

    The URL is validated here as well as in git.clone: a bad URL should be a
    400 on the request the user is watching, not an error row they have to go
    and find.
    """
    url = assert_clone_url(url)
    db = get_db()
    now = int(time.time())
    slug = _unique_slug(db, slug_for_url(url))
    repo_id = str(ULID())
    db.execute(
        'INSERT INTO repos(id, slug, name, remote_url, branch, clone_state,'
        ' is_default, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (repo_id, slug, (name or '').strip() or slug, url, (branch or '').strip(),
         'pending', 0, now, now),
    )
    db.commit()
    return get_repo(repo_id)


def set_state(repo_id: str, state: str, error: str | None = None) -> None:
    db = get_db()
    db.execute(
        'UPDATE repos SET clone_state=?, clone_error=?, updated_at=? WHERE id=?',
        (state, error, int(time.time()), repo_id),
    )
    db.commit()


def record_sync(
    repo_id: str,
    *,
    head_sha: str | None = None,
    branch: str | None = None,
    graph_node_count: int | None = None,
    now: int | None = None,
) -> None:
    """Write what a clone/pull found. Only the fields given are touched, so a
    failed graph build never clears a previously good head sha."""
    now = now or int(time.time())
    sets = ['last_pulled_at=?', 'updated_at=?']
    params: list = [now, now]
    if head_sha is not None:
        sets.append('head_sha=?')
        params.append(head_sha)
    if branch is not None:
        sets.append('branch=?')
        params.append(branch)
    if graph_node_count is not None:
        sets += ['graph_node_count=?', 'graph_built_at=?']
        params += [graph_node_count, now]
    params.append(repo_id)
    db = get_db()
    db.execute(f'UPDATE repos SET {", ".join(sets)} WHERE id=?', params)
    db.commit()


def set_default(repo_id: str) -> None:
    db = get_db()
    db.execute('UPDATE repos SET is_default=0 WHERE is_default=1')
    db.execute('UPDATE repos SET is_default=1, updated_at=? WHERE id=?',
               (int(time.time()), repo_id))
    db.commit()


def delete_repo(repo_id: str) -> bool:
    """Forget a repo and remove its checkout.

    The row goes first: if the rmtree fails (a file being read, a permission
    problem), the user still sees the repo gone from the app rather than a
    half-deleted one they cannot retry.
    """
    repo = get_repo(repo_id)
    if not repo:
        return False
    db = get_db()
    db.execute('DELETE FROM repos WHERE id=?', (repo_id,))
    db.commit()
    storage.delete_repo_dir(repo['slug'])
    return True
