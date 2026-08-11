"""Email HTML is sanitized once at import time; the frontend renders the
stored markup as trusted content — the same contract as fanfic chapters
(backend/fanfic/sanitize.py), and for the same reason: sanitizing on every
render is work repeated forever to defend against bytes that never change.

Pipeline order matters and mirrors the fanfic one: image srcs are rewritten
*first*, then the result is sanitized, so nh3 gets the last word on the
markup that actually ships.

The one thing this does that chapter sanitizing doesn't is take images off
the network entirely. A remote <img> in an email is, far more often than
not, a tracking pixel: fetching it tells the sender the message was opened,
when, and from which IP — which here is a home connection, not a mail
provider's proxy. So no email image is ever loaded from its origin by the
browser. Each src is rewritten to a local `/api/email/images/<url_hash>`
path, and the bytes are fetched once, server-side, by a background worker
(backend/email/images.py) into content-addressed storage.

That rewrite is possible before anything is downloaded because the path is
keyed on a hash of the URL, which is known at import time. The markup is
final immediately; the bytes arrive whenever the worker gets to them.

The result still lands in `data-src` rather than `src`, so nothing loads
until the reader asks — but by then the fetch is same-origin, so opening an
email tells the sender nothing at all.
"""

import re

import nh3
from bs4 import BeautifulSoup

from backend.email.media import url_hash

_ALLOWED_TAGS = {
    'a', 'abbr', 'b', 'blockquote', 'br', 'caption', 'center', 'code', 'dd',
    'div', 'dl', 'dt', 'em', 'figcaption', 'figure', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'hr', 'i', 'img', 'ins', 'li', 'ol', 'p', 'pre', 's', 'small',
    'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th',
    'thead', 'tr', 'u', 'ul',
}

# No `style`, deliberately, though marketing mail leans on it heavily: nh3
# does not parse CSS, so a permitted style attribute passes `url(...)`
# through untouched — which is the tracking fetch this module exists to
# stop, wearing a different hat. Plain-looking mail is the price.
_ALLOWED_ATTRS = {
    'a': {'href', 'title'},
    # data-src, never src — see _defer_images. width/height are kept so a
    # deferred image reserves roughly the right space instead of collapsing.
    'img': {'data-src', 'alt', 'title', 'width', 'height'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan'},
    'ol': {'start'},
}

# Dropped along with their children. <style> matters more here than anywhere
# else in the app: an email's <head> stylesheet is often larger than its
# prose, and letting it through as text would dump CSS into the reader.
_CLEAN_CONTENT_TAGS = {'noscript', 'script', 'style', 'head', 'title'}

_SAFE_SCHEME = re.compile(r'^https?://', re.IGNORECASE)


def _localize_images(html: str) -> tuple[str, list[tuple[str, str]]]:
    """Point every http(s) img at a local path. Returns (html, [(hash, url)]).

    Anything not plainly http(s) — `javascript:`, `data:`, a bare fragment —
    is dropped rather than rewritten. It has to be filtered *here* because nh3
    validates url_schemes only for attributes it knows are URLs; a data-src it
    has never heard of is just a string to it, and the frontend promotes that
    string to a real src when the reader clicks Load images.

    The returned pairs are what the caller queues for download. They are
    deduplicated within the message — a signature logo repeated in a quoted
    reply chain is one fetch, not six.
    """
    soup = BeautifulSoup(html, 'html.parser')
    found: dict[str, str] = {}
    for img in soup.find_all('img'):
        # srcset would reintroduce the remote fetch that rewriting src
        # prevents, and no allowed-attribute list can stop what it doesn't
        # enumerate.
        for attr in ('srcset', 'data-srcset', 'loading'):
            if img.has_attr(attr):
                del img[attr]
        src = (img.get('src') or '').strip()
        if img.has_attr('src'):
            del img['src']
        if _SAFE_SCHEME.match(src):
            digest = url_hash(src)
            found[digest] = src
            img['data-src'] = f'/api/email/images/{digest}'
    return str(soup), list(found.items())


def sanitize_email_html(html: str) -> tuple[str, list[tuple[str, str]]]:
    """Untrusted email HTML -> (safe markup, images to fetch).

    Returns the image list rather than queueing it here so this stays a pure
    function with no DB or network, testable on a fixture string — the same
    line backend/fanfic/xenforo.py draws.
    """
    if not html or not html.strip():
        return '', []
    localized, images = _localize_images(html)
    clean = nh3.clean(
        localized,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        url_schemes={'http', 'https', 'mailto'},
        link_rel='noopener noreferrer nofollow',
    )
    return clean, images
