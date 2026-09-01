"""Lever job boards — public, unauthenticated, one board per company.

Lever returns a top-level JSON array rather than an object, and splits the
posting body across `descriptionPlain` plus a list of `lists` (Requirements,
Benefits, and so on). The lists are where the requirements actually live, so
dropping them would hand the keyword report a posting with no requirements in
it — the one part it exists to read.
"""
from __future__ import annotations

from backend.htmltext import strip_html
from backend.jobs.sources.base import SourceResult, clean_slug, get_json

API_ROOT = 'https://api.lever.co/v0/postings'


def fetch(params: dict, *, creds: dict | None = None) -> SourceResult:
    slug = clean_slug(params.get('slug'))
    payload = get_json(f'{API_ROOT}/{slug}', params={'mode': 'json'})
    rows = [r for r in (payload if isinstance(payload, list) else []) if isinstance(r, dict)]
    locations = [str(x).strip().lower() for x in (params.get('locations') or []) if str(x).strip()]
    if locations:
        rows = [r for r in rows if _in_locations(r, locations)]
    return SourceResult(jobs=[_normalize(r, slug) for r in rows])


def _in_locations(row: dict, wanted: list[str]) -> bool:
    """Whether a posting sits in one of the locations the board URL named.

    Read from `categories.allLocations` rather than `categories.location`: a
    posting open in several cities lists them all there and only the first in
    the singular field, so matching the singular one would drop a Toronto role
    that happened to lead with New York.

    Substring rather than equality, because the URL says `?location=Toronto`
    while the posting says `Toronto, ON` — the hosted board matches these the
    same loose way.
    """
    categories = row.get('categories') or {}
    haystack = [str(x).lower() for x in categories.get('allLocations') or []]
    if categories.get('location'):
        haystack.append(str(categories['location']).lower())
    return any(want in place for place in haystack for want in wanted)


def _description(row: dict) -> str:
    parts = [row.get('descriptionPlain') or strip_html(row.get('description') or '')]
    for section in row.get('lists') or []:
        if not isinstance(section, dict):
            continue
        text = strip_html(section.get('content') or '')
        if text:
            parts.append(f"{section.get('text') or ''}\n{text}".strip())
    return '\n\n'.join(p for p in parts if p)


def _normalize(row: dict, slug: str) -> dict:
    categories = row.get('categories') or {}
    location = categories.get('location') or ''
    workplace = (row.get('workplaceType') or '').lower()

    return {
        'sourceId': str(row.get('id') or ''),
        'title': row.get('text') or '',
        'company': slug,
        'location': location,
        'remote': workplace == 'remote' or 'remote' in location.lower(),
        'salaryMin': None,
        'salaryMax': None,
        'salaryCurrency': '',
        'description': _description(row),
        'url': row.get('hostedUrl') or row.get('applyUrl') or '',
        # Lever sends epoch milliseconds.
        'postedAt': row.get('createdAt'),
        'raw': row,
    }
