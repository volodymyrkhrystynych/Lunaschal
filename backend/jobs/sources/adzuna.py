"""Adzuna — the broad aggregator, and the only source that needs credentials.

Adzuna indexes much of the same inventory as Indeed and LinkedIn, through a
documented API with a free tier, which is why it is here and they are not.

Its `description` is a **truncated snippet**, not the posting body. That is the
one thing to know about this adapter: the keyword report computed at sync time
is therefore computed against a summary, and will understate coverage. Opening
the job runs `ingest.py` against `redirect_url` to fill in the real body and
re-score. Pretending the snippet is the posting would quietly mis-rank the feed.
"""
from __future__ import annotations

from backend.jobs.sources.base import SourceError, SourceResult, coerce_number, get_json

# Country is part of the path, not a query parameter. 'ca' is Canada; the
# user's profile location is not consulted because a search's country is a
# property of the search, not of where the user happens to live.
DEFAULT_COUNTRY = 'ca'
API_ROOT = 'https://api.adzuna.com/v1/api/jobs'

MAX_RESULTS = 50
DEFAULT_MAX_DAYS_OLD = 14

_ALLOWED_COUNTRIES = frozenset({
    'gb', 'us', 'at', 'au', 'be', 'br', 'ca', 'ch', 'de', 'es', 'fr', 'in',
    'it', 'mx', 'nl', 'nz', 'pl', 'sg', 'za',
})


def fetch(params: dict, *, creds: dict | None = None) -> SourceResult:
    creds = creds or {}
    app_id = (creds.get('app_id') or '').strip()
    app_key = (creds.get('app_key') or '').strip()
    if not app_id or not app_key:
        # Not an error: the other three sources work without this, and the feed
        # should not go red because one of four is unconfigured.
        return SourceResult(message='Adzuna needs an app ID and key in Settings.')

    country = (params.get('country') or DEFAULT_COUNTRY).strip().lower()
    if country not in _ALLOWED_COUNTRIES:
        raise SourceError(f'Adzuna has no country {country!r}.')

    query = {
        'app_id': app_id,
        'app_key': app_key,
        'results_per_page': MAX_RESULTS,
        'max_days_old': params.get('maxDaysOld') or DEFAULT_MAX_DAYS_OLD,
        'content-type': 'application/json',
    }
    if params.get('what'):
        query['what'] = params['what']
    if params.get('where'):
        query['where'] = params['where']
    if params.get('distanceKm'):
        query['distance'] = params['distanceKm']
    if params.get('remoteOnly'):
        # Adzuna has no remote flag; this is the documented idiom for it.
        query['what_phrase'] = 'remote'

    payload = get_json(f'{API_ROOT}/{country}/search/1', params=query)
    results = payload.get('results', []) if isinstance(payload, dict) else []
    return SourceResult(jobs=[_normalize(r) for r in results if isinstance(r, dict)])


def _normalize(row: dict) -> dict:
    company = (row.get('company') or {}).get('display_name') or ''
    location = (row.get('location') or {}).get('display_name') or ''
    contract = (row.get('contract_time') or '')

    return {
        'sourceId': str(row.get('id') or ''),
        'title': row.get('title') or '',
        'company': company,
        'location': location,
        # Adzuna exposes no remote field, so this is inference from the text.
        # It is a display hint, never a filter.
        'remote': 'remote' in f"{row.get('title', '')} {location} {contract}".lower(),
        'salaryMin': coerce_number(row.get('salary_min')),
        'salaryMax': coerce_number(row.get('salary_max')),
        'salaryCurrency': '',
        # Snippet, not body — see the module docstring.
        'description': row.get('description') or '',
        'descriptionIsSnippet': True,
        'url': row.get('redirect_url') or '',
        'postedAt': row.get('created') or '',
        'raw': row,
    }
