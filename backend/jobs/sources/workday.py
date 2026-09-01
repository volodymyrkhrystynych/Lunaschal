"""Workday public CXS API discovery, scoped to myworkdayjobs.com hosts."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit
import requests

from backend.htmltext import strip_html
from backend.jobs.sources.base import DESKTOP_UA, REQUEST_TIMEOUT, SourceError, SourceResult

HOST_RE = re.compile(r'^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$', re.I)
SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
LOCALE_RE = re.compile(r'^[a-z]{2}(?:-[A-Z]{2})?$')
MAX_POSTINGS = 200


# Facet parameters are forwarded by *name*, because the name varies per tenant
# and is the tenant's own — but only names that are about place, which is what
# this feature scopes on. An allowlist of exact names would not survive that
# variation (`locations`, `locationCountry`, `Location_Country`,
# `LocationCountry` are four spellings of two facets across the registered
# boards); forwarding everything would send Workday tracking junk as a facet
# and get an empty board back. Matching on the word is what fits both.
_FACET_NAME_RE = re.compile(r'location|country', re.IGNORECASE)
# Facet ids are opaque Workday hashes; anything else is not one.
_FACET_VALUE_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


def parse_board_url(url: str) -> dict:
    """Host, tenant, site — and the facets the URL already scopes the board to.

    Twenty-nine of the thirty registered Workday boards carry a location facet
    in their query string, `?locationCountry=a30a87ed…` being Workday's own
    Canada id. Dropping it meant fetching the board worldwide, which is why
    boards curated to Canada were returning Mumbai and Taipei postings — and,
    because `MAX_POSTINGS` caps the walk at 200, a global board could exhaust
    its budget before reaching a Canadian row at all.

    The facet *parameter name* varies by tenant (`locationCountry`,
    `Location_Country`, `LocationCountry`, `locations`), which is exactly why
    they are forwarded as given rather than normalised: the name in the URL is
    the name the tenant's own board uses.
    """
    parsed = urlsplit(url if '://' in url else f'https://{url}')
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not HOST_RE.fullmatch(host):
        raise SourceError('Workday URL must use an https *.wdN.myworkdayjobs.com host.')
    tenant = host.split('.', 1)[0]
    segments = [segment for segment in parsed.path.split('/') if segment]
    if segments and LOCALE_RE.fullmatch(segments[0]):
        segments.pop(0)
    site = segments[0] if segments else ''
    if not site or not SLUG_RE.fullmatch(site):
        raise SourceError('Could not identify the Workday career-site name from that URL.')

    facets: dict[str, list[str]] = {}
    search_text = ''
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        if key == 'q':
            search_text = values[0].strip()[:128]
            continue
        if not _FACET_NAME_RE.search(key):
            continue
        clean = [v for v in values if _FACET_VALUE_RE.fullmatch(v)]
        if clean:
            facets[key] = clean

    out = {'host': host, 'tenant': tenant, 'site': site}
    if facets:
        out['facets'] = facets
    if search_text:
        out['searchText'] = search_text
    return out


def fetch(params: dict, *, creds: dict | None = None) -> SourceResult:
    host, tenant, site = (params.get(k) or '' for k in ('host', 'tenant', 'site'))
    if not HOST_RE.fullmatch(host) or not SLUG_RE.fullmatch(tenant) or not SLUG_RE.fullmatch(site):
        raise SourceError('Invalid Workday board parameters.')
    root = f'https://{host}/wday/cxs/{tenant}/{site}'
    headers = {'User-Agent': DESKTOP_UA, 'Accept': 'application/json'}
    # The facets the registered URL already chose. Unlike the company boards,
    # this filter is applied by Workday rather than here, so a scoped board
    # spends its 200-posting budget on postings that are actually in scope.
    applied = {k: list(v) for k, v in (params.get('facets') or {}).items()}
    search_text = str(params.get('searchText') or '')
    rows = []
    for offset in range(0, MAX_POSTINGS, 20):
        try:
            response = requests.post(
                f'{root}/jobs', json={'appliedFacets': applied, 'limit': 20,
                                      'offset': offset, 'searchText': search_text},
                headers=headers, timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status(); payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f'Workday board request failed: {exc}') from exc
        batch = payload.get('jobPostings') or []
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 20 or len(rows) >= int(payload.get('total') or 0):
            break
    jobs = []
    for listing in rows[:MAX_POSTINGS]:
        path = listing.get('externalPath') or ''
        if not path.startswith('/'):
            continue
        try:
            detail_response = requests.get(f'{root}{path}', headers=headers,
                                           timeout=REQUEST_TIMEOUT)
            detail_response.raise_for_status(); detail = detail_response.json()
        except (requests.RequestException, ValueError):
            detail = {}
        info = detail.get('jobPostingInfo') or {}
        source_id = str(info.get('jobReqId') or listing.get('bulletFields', [''])[-1] or path)
        external_url = info.get('externalUrl') or path
        if not str(external_url).startswith(('https://', 'http://')):
            external_url = f'https://{host}{external_url}'
        jobs.append({
            'sourceId': f'workday:{host}:{site}:{source_id}',
            'title': info.get('title') or listing.get('title') or '',
            'company': tenant,
            'location': info.get('location') or listing.get('locationsText') or '',
            'remote': 'remote' in f"{info.get('location','')} {listing.get('locationsText','')}".lower(),
            'salaryMin': None, 'salaryMax': None, 'salaryCurrency': '',
            'description': strip_html(info.get('jobDescription') or ''),
            'url': external_url,
            'postedAt': info.get('startDate') or listing.get('postedOn'),
            'raw': {'listing': listing, 'detail': detail},
        })
    return SourceResult(jobs=jobs)
