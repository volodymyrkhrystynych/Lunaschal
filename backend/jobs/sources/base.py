"""Shared plumbing for the source adapters: slug safety, HTTP, result shape."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

# The same desktop string ingest.py sends. These are JSON APIs rather than
# pages, so it changes nothing about the response — it is here so that a
# request from this app is identifiable as one thing, not two.
DESKTOP_UA = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/126.0.0.0 Safari/537.36'
)

REQUEST_TIMEOUT = 20

# Board responses are small JSON documents; a company with 400 openings is
# still well under this. The cap exists so a wrong URL returning a 200 and a
# gigabyte of something else cannot exhaust memory.
MAX_BYTES = 8 * 1024 * 1024

# A board slug goes straight into a URL path. Anything outside this set can
# change which host or path is actually requested — '../' walks the API root,
# and an '@' turns the rest of the URL into userinfo pointing at another host.
_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')


class SourceError(Exception):
    """A source could not be fetched. Carries a message fit to show the user."""


@dataclass
class SourceResult:
    """What one fetch produced.

    `jobs` empty with `message` set is the normal shape for an unconfigured
    source — it is not an error, and the sweep should record it and move on.
    """
    jobs: list[dict] = field(default_factory=list)
    message: str = ''


def clean_slug(value) -> str:
    """Validate a board slug, or raise.

    Rejecting is the only safe option: there is no way to escape a slug into a
    URL path such that a malicious one stays harmless, and a legitimate board
    slug never contains anything outside this set.
    """
    slug = (value or '').strip()
    if not slug:
        raise SourceError('This source needs a board slug.')
    if not _SLUG_RE.match(slug):
        raise SourceError(
            f'Invalid board slug {slug!r} — letters, digits, hyphens and '
            'underscores only.'
        )
    return slug


def get_json(url: str, *, params: dict | None = None) -> dict | list:
    """GET one JSON document from a known-constant host.

    No redirect following: these are stable API endpoints, and a board API that
    suddenly redirects is a sign the URL is wrong rather than something to
    chase.
    """
    try:
        resp = requests.get(
            url,
            params=params,
            headers={'User-Agent': DESKTOP_UA, 'Accept': 'application/json'},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
    except requests.RequestException as e:
        raise SourceError(f'Request failed: {e}') from e

    with resp:
        if resp.status_code == 404:
            raise SourceError('Not found — check the board slug.')
        if resp.status_code >= 400:
            raise SourceError(f'HTTP {resp.status_code}')
        if resp.is_redirect or resp.is_permanent_redirect:
            raise SourceError(f'Unexpected redirect (HTTP {resp.status_code})')

        body = bytearray()
        for chunk in resp.iter_content(8192):
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise SourceError('Response too large')

        try:
            import json
            return json.loads(body.decode('utf-8', errors='replace'))
        except ValueError as e:
            raise SourceError(f'Malformed JSON: {e}') from e


def coerce_number(value):
    """A salary figure, or None. Boards send these as strings as often as not."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    # Zero is how several boards spell "not disclosed"; storing it would render
    # as a real $0 range on the card.
    return num if num > 0 else None
