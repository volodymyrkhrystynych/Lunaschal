"""The two GitHub reads the PR journal makes: closed PRs, and one PR's commits.

The repository is public, so the token is optional. Without one GitHub allows
60 requests an hour, which the first backfill (one commits request per PR)
outruns; `RateLimited` is how the sync learns to stop for this tick and finish
the rest on later ones.
"""
from __future__ import annotations

import requests

from backend.codeagent.github import API, GitHubError

TIMEOUT = 30
PER_PAGE = 100


class RateLimited(GitHubError):
    pass


def _headers(token: str | None) -> dict:
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def _get(url: str, token: str | None, params: dict) -> list:
    try:
        resp = requests.get(url, headers=_headers(token), params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GitHubError(f'GitHub is unreachable: {e}') from e
    if resp.status_code in (403, 429) and (
            resp.headers.get('X-RateLimit-Remaining') == '0' or resp.status_code == 429):
        raise RateLimited('GitHub rate limit reached')
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
