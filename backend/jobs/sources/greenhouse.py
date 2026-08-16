"""Greenhouse job boards — public, unauthenticated, one board per company.

`?content=true` returns the full posting body HTML inline, which means one
request per company rather than one per posting. That is the whole reason the
company-board sources are worth having: the description is complete, so the
keyword report computed at sync is the real one.
"""
from __future__ import annotations

from backend.htmltext import strip_html
from backend.jobs.sources.base import SourceResult, clean_slug, get_json

API_ROOT = 'https://boards-api.greenhouse.io/v1/boards'


def fetch(params: dict, *, creds: dict | None = None) -> SourceResult:
    slug = clean_slug(params.get('slug'))
    payload = get_json(f'{API_ROOT}/{slug}/jobs', params={'content': 'true'})
    rows = payload.get('jobs', []) if isinstance(payload, dict) else []
    return SourceResult(
        jobs=[_normalize(r, slug) for r in rows if isinstance(r, dict)]
    )


def _normalize(row: dict, slug: str) -> dict:
    offices = row.get('offices') or []
    location = (row.get('location') or {}).get('name') or ''
    if not location and offices:
        location = offices[0].get('name') or ''

    # Greenhouse HTML-escapes the body and then sends it as a string, so it
    # needs unescaping before stripping or the text is full of &lt;p&gt;.
    import html as html_mod
    body_html = html_mod.unescape(row.get('content') or '')

    # `company_name` is a plain string on the real API. The slug is the last
    # resort and a poor one — Ada's board is `ada18`, so falling back to it
    # labels every posting with a number the user has never seen.
    company = (row.get('company_name') or '').strip()
    if not company and isinstance(row.get('company'), dict):
        company = (row['company'].get('name') or '').strip()

    return {
        'sourceId': str(row.get('id') or ''),
        'title': row.get('title') or '',
        'company': company or slug,
        'location': location,
        'remote': 'remote' in f'{location} {row.get("title", "")}'.lower(),
        'salaryMin': None,
        'salaryMax': None,
        'salaryCurrency': '',
        'description': strip_html(body_html),
        'url': row.get('absolute_url') or '',
        # `first_published` is when the posting went up; `updated_at` moves
        # every time someone edits a typo, which would keep re-floating old
        # postings to the top of a feed sorted by recency.
        'postedAt': row.get('first_published') or row.get('updated_at') or '',
        'raw': row,
    }
