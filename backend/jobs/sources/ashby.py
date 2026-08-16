"""Ashby job boards — public, unauthenticated, one board per company.

`?includeCompensation=true` is asked for because Ashby is the only one of the
three company boards that *can* return a pay range. In practice it usually does
not: checked against a real board, every posting carried a `compensation`
object whose `summaryComponents` was empty. So the parameter is worth sending
and the range is worth reading when present, but the card cannot rely on it.
"""
from __future__ import annotations

from backend.htmltext import strip_html
from backend.jobs.sources.base import SourceResult, clean_slug, coerce_number, get_json

API_ROOT = 'https://api.ashbyhq.com/posting-api/job-board'


def fetch(params: dict, *, creds: dict | None = None) -> SourceResult:
    slug = clean_slug(params.get('slug'))
    payload = get_json(f'{API_ROOT}/{slug}', params={'includeCompensation': 'true'})
    rows = payload.get('jobs', []) if isinstance(payload, dict) else []
    return SourceResult(
        jobs=[_normalize(r, slug) for r in rows if isinstance(r, dict)]
    )


def _salary(row: dict) -> tuple[float | None, float | None, str]:
    """Ashby nests the range under compensation.compensationTiers[].components.

    Returns (min, max, currency) — all None/'' when the posting has no range,
    which is most of them.
    """
    comp = row.get('compensation')
    if not isinstance(comp, dict):
        return None, None, ''
    summary = comp.get('summaryComponents') or []
    for component in summary:
        if not isinstance(component, dict):
            continue
        if (component.get('compensationType') or '') != 'Salary':
            continue
        return (
            coerce_number(component.get('minValue')),
            coerce_number(component.get('maxValue')),
            component.get('currencyCode') or '',
        )
    return None, None, ''


def _normalize(row: dict, slug: str) -> dict:
    salary_min, salary_max, currency = _salary(row)
    location = row.get('location') or ''

    return {
        'sourceId': str(row.get('id') or ''),
        'title': row.get('title') or '',
        'company': slug,
        'location': location,
        'remote': bool(row.get('isRemote')) or 'remote' in location.lower(),
        'salaryMin': salary_min,
        'salaryMax': salary_max,
        'salaryCurrency': currency,
        'description': (
            row.get('descriptionPlain')
            or strip_html(row.get('descriptionHtml') or '')
        ),
        'url': row.get('jobUrl') or row.get('applyUrl') or '',
        'postedAt': row.get('publishedAt') or '',
        'raw': row,
    }
