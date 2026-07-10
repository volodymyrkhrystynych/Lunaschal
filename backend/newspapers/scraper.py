"""Fetches today's front-page cover image URL and bytes from frontpages.com.

frontpages.com only ever serves *whatever it currently has live* for a given
paper — the `?d=YYYY-MM-DD` query param seen in its share links is inert,
there is no real archive on the site. Critically, "live" isn't guaranteed to
mean *our* calendar today: the image path itself is stamped with the date
frontpages.com considers the edition to be for (e.g.
`/g/2026/07/09/toronto-star-...webp`), and that can still be yesterday's
edition for a while after our local midnight if the paper hasn't published a
new front page yet. Callers must use `extract_date` on the resolved image
URL as the source of truth for which day an edition belongs to rather than
assuming it matches the caller's own clock (see sync.py).

The page's `og:image` meta tag is a decoy that 404s — the real cover image
path is written into the DOM by a tiny inline script that base64-decodes it
into the `#giornale-img` element's `src` (the same asset every visitor's
browser loads to render the page; this just does the same base64 decode
without a JS engine).
"""

import base64
import re
from urllib.parse import urljoin

# A plain browser UA: unlike some fanfic-hosting forums, frontpages.com does
# not appear to challenge non-browser clients, but this keeps requests
# consistent with the rest of the codebase's scraping conventions.
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_ATOB_RE = re.compile(r"atob\('([^']+)'\)")
_URL_DATE_RE = re.compile(r'/(\d{4})/(\d{2})/(\d{2})/')


def _headers() -> dict:
    return {'User-Agent': USER_AGENT}


def fetch_image_url(page_url: str) -> str:
    import requests

    resp = requests.get(page_url, timeout=15, headers=_headers())
    resp.raise_for_status()
    match = _ATOB_RE.search(resp.text)
    if not match:
        raise ValueError(f'could not find cover image script on {page_url}')
    path = base64.b64decode(match.group(1)).decode()
    return urljoin(page_url, path)


def extract_date(image_url: str) -> str | None:
    """Pull the YYYY-MM-DD the edition is dated, out of its image URL path
    (e.g. `.../g/2026/07/09/toronto-star-...webp` -> '2026-07-09'). Returns
    None if the URL doesn't contain the expected date segments, so callers
    can fall back to their own clock."""
    match = _URL_DATE_RE.search(image_url)
    if not match:
        return None
    return '-'.join(match.groups())


def download_image(image_url: str) -> tuple[bytes, str]:
    import requests

    with requests.get(image_url, timeout=30, headers=_headers(), stream=True) as resp:
        resp.raise_for_status()
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ValueError(f'image exceeds {MAX_IMAGE_BYTES} bytes: {image_url}')
            chunks.append(chunk)
        return b''.join(chunks), resp.headers.get('Content-Type', '')
