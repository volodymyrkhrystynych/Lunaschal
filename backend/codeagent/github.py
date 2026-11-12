"""Opening the pull request for a pushed agent branch, as the machine account.

The machine account is a collaborator on the repo but not on the `main`
ruleset's bypass list, so the PR is the only way its work reaches `main` —
merged by the owner. See docs/code-agent.md for the one-time setup.
"""
from __future__ import annotations

import requests

API = 'https://api.github.com'
TIMEOUT = 30


class GitHubError(RuntimeError):
    pass


def headers(token: str | None) -> dict:
    """GitHub REST headers; no Authorization when there is no token, which is
    enough for reading a public repository (backend/github_prs/)."""
    out = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        out['Authorization'] = f'Bearer {token}'
    return out


def open_pull_request(token: str, owner: str, repo: str, *, head: str, base: str,
                      title: str, body: str) -> str:
    """Open (or find the already-open) PR for `head` and return its URL."""
    url = f'{API}/repos/{owner}/{repo}/pulls'
    try:
        resp = requests.post(url, headers=headers(token), timeout=TIMEOUT, json={
            'title': title, 'head': head, 'base': base, 'body': body, 'draft': False,
        })
    except requests.RequestException as e:
        raise GitHubError(f'GitHub is unreachable: {e}') from e
    if resp.status_code == 201:
        return resp.json()['html_url']
    if resp.status_code == 422:
        # Most often "a pull request already exists" — a second push of the
        # same task. Return that one rather than failing the push.
        existing = requests.get(url, headers=headers(token), timeout=TIMEOUT,
                                params={'head': f'{owner}:{head}', 'state': 'open'})
        if existing.ok and existing.json():
            return existing.json()[0]['html_url']
    try:
        message = resp.json().get('message') or resp.text
    except ValueError:
        message = resp.text
    raise GitHubError(f'GitHub refused the pull request ({resp.status_code}): {message[:300]}')


def pr_body(task: dict) -> str:
    tests = task.get('testExit')
    verdict = ('passed' if tests == 0 else f'failed (exit {tests})') if tests is not None \
        else 'not run'
    parts = [
        '## Task', task.get('prompt') or '',
        '## Plan', task.get('plan') or '_none recorded_',
        '## Summary', task.get('summary') or '_none_',
        f'## Tests\nRun by Lunaschal after the agent finished: **{verdict}**.',
        f"---\n🤖 Written by Lunaschal's local code agent (`{task.get('harness')}`) "
        'and reviewed on the phone before pushing.',
    ]
    return '\n\n'.join(parts)
