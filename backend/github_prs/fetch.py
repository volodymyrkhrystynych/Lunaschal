"""The two GitHub reads the PR journal makes: closed PRs, and one PR's commits.

The repository is public, so the token is optional. Without one GitHub allows
60 requests an hour, which the first backfill (one commits request per PR)
outruns; `RateLimited` is how the sync learns to stop for this tick and finish
the rest on later ones.

A private repository needs a token that can read its pull requests; without
one GitHub answers 404, exactly as for a name that does not exist, so
`NotFound` says both.
"""
from __future__ import annotations

import requests

from backend.codeagent.github import API, GitHubError, headers

TIMEOUT = 30
PER_PAGE = 100


class RateLimited(GitHubError):
    pass


class Refused(GitHubError):
    """An answer asking again will not change (404, 410, 422), unlike a 5xx,
    a dropped connection or a token problem — the sync files the PR without what it could not read rather
    than let it block every PR merged after it."""


class NotFound(Refused):
    pass


def _get(url: str, token: str | None, params: dict) -> list:
    try:
        resp = requests.get(url, headers=headers(token), params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GitHubError(f'GitHub is unreachable: {e}') from e
    # A secondary limit is a 403 with Retry-After and quota left over.
    if resp.status_code == 429 or (resp.status_code == 403 and (
            resp.headers.get('X-RateLimit-Remaining') == '0' or 'Retry-After' in resp.headers)):
        raise RateLimited('GitHub rate limit reached')
    if resp.status_code == 404:
        raise NotFound(f'GitHub has no {url} — misspelled, or private without a token that can read it')
    # Not 401/403: a token problem is the whole repository's, and fixable.
    if resp.status_code in (410, 422):
        raise Refused(f'GitHub answered {resp.status_code} for {url}: {resp.text[:200]}')
    if not resp.ok:
        raise GitHubError(f'GitHub answered {resp.status_code} for {url}: {resp.text[:200]}')
    return resp.json()


def closed_pulls(token: str | None, owner: str, repo: str, page: int) -> list[dict]:
    """One page of closed PRs, most recently updated first."""
    return _get(f'{API}/repos/{owner}/{repo}/pulls', token, {
        'state': 'closed', 'sort': 'updated', 'direction': 'desc',
        'per_page': PER_PAGE, 'page': page,
    })


def pull_commits(token: str | None, owner: str, repo: str, number: int) -> list[dict]:
    """Every commit on a PR (GitHub stops listing at 250)."""
    commits: list[dict] = []
    page = 1
    while True:
        batch = _get(f'{API}/repos/{owner}/{repo}/pulls/{number}/commits', token,
                     {'per_page': PER_PAGE, 'page': page})
        commits.extend(batch)
        if len(batch) < PER_PAGE:
            return commits
        page += 1
