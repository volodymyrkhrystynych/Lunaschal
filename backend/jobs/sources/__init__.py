"""Job sources: one adapter per board, all behind `fetch(params, *, creds)`.

Every adapter returns the same shape — a list of dicts carrying `sourceId`,
`title`, `company`, `location`, `remote`, `salaryMin`/`salaryMax`/
`salaryCurrency`, `description`, `url`, `postedAt`, and `raw`. `sync.py` does
not know or care which board a row came from.

Two things separate these from `ingest.py`, which fetches a URL the user typed:

- **The hosts are fixed constants**, so there is no SSRF surface and no
  `assert_public_url`. The board *slug* is user input, but it is validated to
  `[A-Za-z0-9_-]+` before it goes anywhere near a URL — see `clean_slug`.
- **A misconfigured source is not an error.** Adzuna without credentials, or a
  board slug that does not exist, returns an empty list and a message. The feed
  is expected to work while three of four sources are unconfigured.

No adapter touches the database or the model.
"""
from backend.jobs.sources.base import (
    DESKTOP_UA,
    REQUEST_TIMEOUT,
    SourceError,
    SourceResult,
    clean_slug,
    get_json,
)

__all__ = [
    'DESKTOP_UA',
    'REQUEST_TIMEOUT',
    'SourceError',
    'SourceResult',
    'clean_slug',
    'get_json',
    'ADAPTERS',
    'fetch',
]


def _adapters():
    from backend.jobs.sources import adzuna, ashby, greenhouse, lever
    return {
        'adzuna': adzuna.fetch,
        'greenhouse': greenhouse.fetch,
        'lever': lever.fetch,
        'ashby': ashby.fetch,
    }


ADAPTERS = ('adzuna', 'greenhouse', 'lever', 'ashby')


def fetch(kind: str, params: dict, *, creds: dict | None = None) -> SourceResult:
    """Dispatch to one adapter. Unknown kinds are a caller bug, so they raise."""
    adapters = _adapters()
    if kind not in adapters:
        raise SourceError(f'Unknown job source: {kind}')
    return adapters[kind](params or {}, creds=creds or {})
