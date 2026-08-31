#!/usr/bin/env python3
"""A company list in Markdown → `job_searches` rows.

`docs/toronto-tech-companies.md` is 220 companies with a careers link each.
Registering them one at a time through Settings is an afternoon of typing, and
the URLs are already in the file — so this reads them out.

**It reuses `resolve.find_candidates` rather than growing its own regexes.**
That module already knows every shape a board URL comes in (the Greenhouse
embed form carries the slug in `?for=`, the legacy `boards.` host and the
current `job-boards.` one, the raw board APIs) and already refuses the path
segments that merely look like slugs. A second copy of that knowledge here is
how the two drift.

It does **not** verify against the live board the way `resolve.py` does, for
the reason `resolve.py` does: verification is a network call per company, and
220 of them is a rate-limit incident. The first scheduled sweep verifies them
in the ordinary way — a bad slug lands in `last_error` and shows red in the
sources panel, which is exactly where a person would look.

Dry run by default. Nothing is written without `--commit`.

    .venv/bin/python scripts/import-company-boards.py
    .venv/bin/python scripts/import-company-boards.py --commit --interval-hours 24

No route, no button, no scheduler entry — `backfill.py`'s precedent. This is a
one-off, and a one-off with a button is a button nobody understands in a year.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.jobs import resolve  # noqa: E402
from backend.jobs.sources import SourceError, clean_slug, workday  # noqa: E402

DEFAULT_DOC = Path('docs/toronto-tech-companies.md')

# "- **[Name](…)** — blurb. **N jobs**. [Careers](URL)"
_ENTRY = re.compile(
    r'^\s*-\s+\*\*\[(?P<name>[^\]]+)\]\([^)]*\)\*\*.*?\[Careers\]\((?P<url>[^)]+)\)',
    re.MULTILINE,
)


def parse_entries(text: str) -> list[tuple[str, str]]:
    """(company, careers_url) for every list entry that has both."""
    return [(m.group('name').strip(), m.group('url').strip())
            for m in _ENTRY.finditer(text)]


def classify(name: str, url: str) -> dict:
    """One entry → what it is: syncable, a named unsupported ATS, or unknown.

    The URL is passed to `find_candidates` as the *final_url* argument, which
    is the slot it was built for — a careers page that redirects straight to a
    board carries the whole answer in the URL, which is precisely this file's
    situation for the majority of rows.
    """
    for kind, slug in resolve.find_candidates('', url):
        try:
            clean_slug(slug)
        except SourceError:
            continue
        return {'company': name, 'url': url, 'kind': kind, 'slug': slug}

    # Workday is listed as unsupported by `resolve.py` because it is not one of
    # the four `job_searches.kind` values — that column has a baked-in CHECK
    # and SQLite cannot ALTER one. `workday_watch.py` exists precisely to poll
    # these without widening it, so they are syncable here even though the
    # resolver correctly declines to call them a saved search.
    if 'myworkdayjobs.com' in url.lower():
        try:
            return {'company': name, 'url': url, 'kind': 'workday', 'slug': '',
                    'params': workday.parse_board_url(url)}
        except SourceError as e:
            return {'company': name, 'url': url, 'kind': None, 'slug': '',
                    'detected': f'Workday, unreadable URL: {e}'}

    detected = resolve.find_unsupported('', url)
    return {'company': name, 'url': url, 'kind': None, 'slug': '',
            'detected': detected}


def plan(entries: list[tuple[str, str]]) -> tuple[list[dict], list[dict]]:
    """(syncable, skipped), with syncable deduplicated on (kind, slug).

    Two companies can share a board — holding companies list their portfolio
    under one slug — and a duplicate saved search would fetch the same board
    twice every night for no new postings.
    """
    syncable: list[dict] = []
    skipped: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for name, url in entries:
        row = classify(name, url)
        if not row['kind']:
            skipped.append(row)
            continue
        params = row.get('params') or {}
        key = (row['kind'],
               row['slug'].lower() or f"{params.get('tenant')}/{params.get('site')}")
        if key in seen:
            row['detected'] = f'duplicate of {key[0]}/{key[1]}'
            skipped.append(row)
            continue
        seen.add(key)
        syncable.append(row)

    return syncable, skipped


def commit(rows: list[dict], *, interval_hours: int, enabled: bool) -> dict:
    """Insert the plan as saved searches. Existing (kind, slug) pairs are kept.

    Idempotent on re-run for the reason every sync path here is: the file will
    be re-reviewed, and a second import must not double every source.
    """
    from ulid import ULID

    from backend.db.connection import get_db

    db = get_db()
    existing = set()
    for row in db.execute('SELECT kind, params FROM job_searches').fetchall():
        try:
            slug = (json.loads(row['params'] or '{}') or {}).get('slug') or ''
        except ValueError:
            slug = ''
        existing.add((row['kind'], slug.lower()))

    # Workday lives in its own table, so its "already present" set is separate.
    existing_workday = {
        (row['url'] or '').lower()
        for row in db.execute('SELECT url FROM workday_boards').fetchall()
    }

    now = int(time.time())
    added = 0
    for row in rows:
        if row['kind'] == 'workday':
            if row['url'].lower() in existing_workday:
                continue
            db.execute(
                'INSERT INTO workday_boards (id, url, label, params, enabled,'
                ' interval_hours, created_at, updated_at)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (str(ULID()), row['url'], row['company'],
                 json.dumps(row['params']), 1 if enabled else 0,
                 interval_hours, now, now),
            )
            added += 1
            continue

        if (row['kind'], row['slug'].lower()) in existing:
            continue
        db.execute(
            'INSERT INTO job_searches (id, kind, label, params, enabled,'
            ' interval_hours, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (str(ULID()), row['kind'], row['company'],
             json.dumps({'slug': row['slug']}), 1 if enabled else 0,
             interval_hours, now, now),
        )
        added += 1
    db.commit()
    return {'added': added, 'alreadyPresent': len(rows) - added}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('doc', nargs='?', type=Path, default=DEFAULT_DOC)
    parser.add_argument('--commit', action='store_true',
                        help='write the rows; without it nothing is changed')
    parser.add_argument('--interval-hours', type=int, default=24)
    parser.add_argument('--disabled', action='store_true',
                        help='create the sources switched off, to enable by hand')
    parser.add_argument('--show-skipped', action='store_true')
    args = parser.parse_args()

    if not args.doc.exists():
        print(f'No such file: {args.doc}', file=sys.stderr)
        return 1

    entries = parse_entries(args.doc.read_text())
    syncable, skipped = plan(entries)

    by_kind = Counter(row['kind'] for row in syncable)
    print(f'{len(entries)} companies in {args.doc}')
    print(f'  syncable: {len(syncable)}  ' +
          '  '.join(f'{k}={n}' for k, n in sorted(by_kind.items())))
    print(f'  skipped : {len(skipped)}')

    reasons = Counter(row.get('detected') or 'unrecognised' for row in skipped)
    for reason, count in reasons.most_common():
        print(f'      {count:>4}  {reason}')

    if args.show_skipped:
        print()
        for row in skipped:
            print(f"  - {row['company']}: {row.get('detected') or 'unrecognised'}"
                  f"\n      {row['url']}")

    if not args.commit:
        print('\nDry run. Re-run with --commit to create these saved searches.')
        return 0

    if not os.environ.get('DATABASE_URL'):
        print('\nWriting to the default database (./data/lunaschal.db).')
    result = commit(syncable, interval_hours=args.interval_hours,
                    enabled=not args.disabled)
    print(f"\nAdded {result['added']} saved searches "
          f"({result['alreadyPresent']} already present).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
