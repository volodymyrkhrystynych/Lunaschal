"""Workday public CXS API discovery, scoped to myworkdayjobs.com hosts."""
from __future__ import annotations

import re
from urllib.parse import urlsplit
import requests

from backend.htmltext import strip_html
from backend.jobs.sources.base import DESKTOP_UA, REQUEST_TIMEOUT, SourceError, SourceResult

HOST_RE = re.compile(r'^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$', re.I)
SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
LOCALE_RE = re.compile(r'^[a-z]{2}(?:-[A-Z]{2})?$')
MAX_POSTINGS = 200


def parse_board_url(url: str) -> dict:
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
    return {'host': host, 'tenant': tenant, 'site': site}


def fetch(params: dict, *, creds: dict | None = None) -> SourceResult:
    host, tenant, site = (params.get(k) or '' for k in ('host', 'tenant', 'site'))
    if not HOST_RE.fullmatch(host) or not SLUG_RE.fullmatch(tenant) or not SLUG_RE.fullmatch(site):
        raise SourceError('Invalid Workday board parameters.')
    root = f'https://{host}/wday/cxs/{tenant}/{site}'
    headers = {'User-Agent': DESKTOP_UA, 'Accept': 'application/json'}
    rows = []
    for offset in range(0, MAX_POSTINGS, 20):
        try:
            response = requests.post(
                f'{root}/jobs', json={'appliedFacets': {}, 'limit': 20,
                                      'offset': offset, 'searchText': ''},
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
