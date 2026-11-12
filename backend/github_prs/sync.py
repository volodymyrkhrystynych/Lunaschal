"""Turn newly merged PRs into journal entries.

The pure half (`merged_from`, `commit_days`, `entry_text`) decides what an
entry says; `sync` does the reads and the writes.

Paging is ordered by `updated_at`, which a merge always bumps, so an
incremental sync can stop at the first page older than the newest merge
already recorded. New PRs are then journaled **oldest first**: if the rate
limit ends a tick halfway through a backfill, every PR still unrecorded is
newer than everything recorded, so that same cursor still finds them.

Each repository in `settings.pr_journal_repos` syncs on its own cursor, and a
failure in one (a typo, a private repository the token cannot read, GitHub
down) is logged and leaves the others to run. A rate limit is the token's, not
the repository's, so it ends the whole tick.

    python -m backend.github_prs.sync --dry-run --repo owner/name

lists what would be journaled without touching the database.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import date, datetime

from ulid import ULID

from backend.day_boundary import day_key_for
from backend.github_prs import fetch

logger = logging.getLogger(__name__)

SUMMARY_JOB = 'github_prs.summarize'
TAGS = ['coding']
# A merge bumps `updated_at` to the merge moment or later; the slack only
# covers clock disagreement between our row and GitHub's.
CURSOR_SLACK = 86400

_REPO = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+')


def parse_ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp())


def merged_from(pulls: list[dict]) -> list[dict]:
    """The merged PRs on a page — a PR closed without merging has no `merged_at`."""
    return [{
        'number': p['number'],
        'title': p.get('title') or '',
        'url': p.get('html_url') or '',
        'body': p.get('body') or '',
        'merged_at': parse_ts(p['merged_at']),
    } for p in pulls if p.get('merged_at')]


def commit_days(commits: list[dict]) -> list[dict]:
    """How many commits were authored on each 4am day, oldest day first.

    Author date, not committer date: a rebase rewrites the committer date of
    every commit to the moment of the rebase, which is exactly the clustering
    this list exists to see through.
    """
    counts: dict[str, int] = {}
    for c in commits:
        info = c.get('commit') or {}
        stamp = (info.get('author') or {}).get('date') or (info.get('committer') or {}).get('date')
        if not stamp:
            continue
        day = day_key_for(parse_ts(stamp))
        counts[day] = counts.get(day, 0) + 1
    return [{'day': d, 'count': counts[d]} for d in sorted(counts)]


def _day_label(day: str, merge_year: int) -> str:
    d = date.fromisoformat(day)
    label = f'{d:%b} {d.day}'
    return label if d.year == merge_year else f'{label}, {d.year}'


def entry_title(pr: dict) -> str:
    return f"Merged #{pr['number']}: {pr['title']}"


def entry_text(pr: dict, days: list[dict], summary: str | None = None) -> str:
    """The entry's body: summary (once written), commits per day, link."""
    merge_year = date.fromisoformat(day_key_for(pr['merged_at'])).year
    parts = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if days:
        lines = [f"{_day_label(d['day'], merge_year)} — {d['count']} "
                 f"commit{'' if d['count'] == 1 else 's'}" for d in days]
        parts.append('Commits:\n' + '\n'.join(lines))
    if pr.get('url'):
        parts.append(pr['url'])
    return '\n\n'.join(parts) or entry_title(pr)


def parse_repo(text) -> str | None:
    """`owner/name` from what a person pastes — the bare name, or the page or
    clone URL — or None when it is neither."""
    value = str(text or '').strip()
    value = re.sub(r'^(?:https?://)?(?:www\.)?github\.com/', '', value, flags=re.I)
    value = value.rstrip('/')
    value = value[:-4] if value.lower().endswith('.git') else value
    return value if _REPO.fullmatch(value) and not value.endswith(('/.', '/..')) else None


def parse_repos(values) -> list[str]:
    """Normalised and deduplicated (GitHub names ignore case); raises
    ValueError naming the first entry that is not a repository."""
    if not isinstance(values, list):
        raise ValueError('expected a list of repositories')
    out: list[str] = []
    for v in values:
        repo = parse_repo(v)
        if repo is None:
            raise ValueError(f'Not a GitHub repository: {v!r} (expected owner/name)')
        if repo.lower() not in {r.lower() for r in out}:
            out.append(repo)
    return out


def stored_repos(raw) -> list[str]:
    try:
        return parse_repos(json.loads(raw or '[]'))
    except ValueError:  # JSONDecodeError included
        return []


def settings(db) -> tuple[list[str], str]:
    row = db.execute('SELECT pr_journal_repos, github_agent_token FROM settings LIMIT 1').fetchone()
    if not row:
        return [], ''
    return stored_repos(row['pr_journal_repos']), (row['github_agent_token'] or '').strip()


def _new_merges(token: str | None, owner: str, name: str, cursor: int | None) -> list[dict]:
    found: list[dict] = []
    page = 1
    while True:
        pulls = fetch.closed_pulls(token, owner, name, page)
        found.extend(merged_from(pulls))
        if len(pulls) < fetch.PER_PAGE:
            break
        if cursor is not None and parse_ts(pulls[-1]['updated_at']) < cursor - CURSOR_SLACK:
            break
        page += 1
    if cursor is not None:
        found = [p for p in found if p['merged_at'] >= cursor - CURSOR_SLACK]
    return sorted(found, key=lambda p: p['merged_at'])


def sync(db=None) -> int:
    """Journal every merged PR not yet recorded, in every configured
    repository. Returns how many were added."""
    from backend.db.connection import get_db

    db = db or get_db()
    repos, token = settings(db)
    added = 0
    for repo in repos:
        try:
            count, limited = _sync_repo(db, repo, token)
        except fetch.RateLimited:
            count, limited = 0, True
        except fetch.GitHubError as e:
            logger.warning('PR journal: %s skipped this tick: %s', repo, e)
            continue
        added += count
        if limited:
            logger.info('PR journal: rate limited at %s; resuming next tick', repo)
            break
    return added


def _sync_repo(db, repo: str, token: str) -> tuple[int, bool]:
    """(PRs filed, whether the rate limit ended it)."""
    from backend.ai import jobs
    from backend.routes.journal import create_journal_entry

    owner, name = repo.split('/', 1)
    cursor = db.execute('SELECT MAX(merged_at) FROM merged_prs WHERE repo=?', (repo,)).fetchone()[0]
    added = 0
    for pr in _new_merges(token, owner, name, cursor):
        if db.execute('SELECT 1 FROM merged_prs WHERE repo=? AND number=?',
                      (repo, pr['number'])).fetchone():
            continue
        try:
            days = commit_days(fetch.pull_commits(token, owner, name, pr['number']))
        except fetch.Refused as e:
            # Asking again gets the same answer, and PRs are filed oldest
            # first: waiting for this one would hold back every later merge.
            logger.warning('PR journal: %s#%s filed without its commits: %s', repo, pr['number'], e)
            days = []
        except fetch.RateLimited:
            return added, True
        except fetch.GitHubError as e:
            # Down or unreachable: retry from this PR next tick.
            logger.warning('PR journal: %s#%s commits unavailable: %s', repo, pr['number'], e)
            break
        entry_id = str(ULID())
        # The ledger row and the entry commit together: create_journal_entry
        # commits the shared connection, and this insert is already in it.
        db.execute(
            'INSERT INTO merged_prs (repo, number, title, url, body, merged_at, commit_days,'
            ' entry_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (repo, pr['number'], pr['title'], pr['url'], pr['body'], pr['merged_at'],
             json.dumps(days), entry_id, int(time.time())),
        )
        create_journal_entry(entry_text(pr, days), None, pr['merged_at'],
                             title=entry_title(pr), tags=TAGS, entry_id=entry_id)
        if pr['body'].strip():
            jobs.enqueue(SUMMARY_JOB, entry_id, {'repo': repo, 'number': pr['number']})
        added += 1
    return added, False


def summarize(entry_id: str, repo: str, number: int) -> None:
    """Write the model's summary into a PR's entry — unless it was edited by hand."""
    from backend.ai.github_prs import summarize_pr
    from backend.db.connection import get_db

    db = get_db()
    row = db.execute('SELECT * FROM merged_prs WHERE repo=? AND number=?', (repo, number)).fetchone()
    if not row or not (row['body'] or '').strip():
        return
    pr = {'number': row['number'], 'title': row['title'], 'url': row['url'],
          'merged_at': row['merged_at']}
    days = json.loads(row['commit_days'] or '[]')
    summary = summarize_pr(row['title'], row['body'])
    if summary:
        db.execute('UPDATE journal_entries SET content=?, updated_at=? WHERE id=? AND content=?',
                   (entry_text(pr, days, summary), int(time.time()), entry_id, entry_text(pr, days)))
    db.execute('UPDATE merged_prs SET summarized_at=? WHERE repo=? AND number=?',
               (int(time.time()), repo, number))
    db.commit()


def _dry_run(repo: str, token: str | None, sample: int) -> None:
    owner, name = repo.split('/', 1)
    merges = _new_merges(token, owner, name, None)
    print(f'{len(merges)} merged PRs in {repo}')
    for pr in merges[-sample:]:
        days = commit_days(fetch.pull_commits(token, owner, name, pr['number']))
        print(f"\n[{day_key_for(pr['merged_at'])}] {entry_title(pr)}\n{entry_text(pr, days)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dry-run', action='store_true', required=True,
                        help='list from GitHub only; never writes the database')
    parser.add_argument('--repo', required=True, type=parse_repo, help='owner/name')
    parser.add_argument('--sample', type=int, default=5,
                        help='how many of the newest PRs to fetch commits for')
    args = parser.parse_args()
    _dry_run(args.repo, os.environ.get('GITHUB_TOKEN'), args.sample)
