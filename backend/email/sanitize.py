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

Email layout depends heavily on inline CSS. nh3 parses declarations property
by property, so we retain a visual-only subset while dropping properties that
can fetch URLs or escape the message's layout box. The frontend supplies a
second boundary by rendering the fragment in a sandboxed iframe.
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
    'thead', 'tr', 'u', 'ul', 'font',
}

_ALLOWED_ATTRS = {
    '*': {'style'},
    'a': {'href', 'title'},
    # data-src, never src — see _defer_images. width/height are kept so a
    # deferred image reserves roughly the right space instead of collapsing.
    'img': {'data-src', 'alt', 'title', 'width', 'height'},
    'table': {'width', 'height', 'cellpadding', 'cellspacing', 'border', 'align', 'bgcolor'},
    'tbody': {'align', 'valign'},
    'tr': {'align', 'valign', 'bgcolor'},
    'td': {'colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'bgcolor'},
    'th': {'colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'bgcolor'},
    'ol': {'start'},
    'font': {'color', 'face', 'size'},
}

# No URL-bearing, viewport-escaping, or overlay properties (background-image,
# content, position, z-index, transform, filter, behavior). CSS cannot make a
# tracking request or cover application UI.
_ALLOWED_STYLE_PROPERTIES = {
    'background-color', 'border', 'border-bottom', 'border-color',
    'border-left', 'border-radius', 'border-right', 'border-style', 'border-top',
    'border-width', 'box-sizing', 'color', 'direction', 'display', 'float',
    'font', 'font-family', 'font-size', 'font-style', 'font-weight', 'height',
    'letter-spacing', 'line-height', 'margin', 'margin-bottom', 'margin-left',
    'margin-right', 'margin-top', 'max-height', 'max-width', 'min-height',
    'min-width', 'opacity', 'overflow', 'overflow-x', 'overflow-y', 'padding',
    'padding-bottom', 'padding-left', 'padding-right', 'padding-top',
    'table-layout', 'text-align', 'text-decoration', 'text-indent',
    'text-transform', 'vertical-align', 'white-space', 'width', 'word-break',
    'word-spacing', 'overflow-wrap',
}

# Dropped along with their children. <style> matters more here than anywhere
# else in the app: an email's <head> stylesheet is often larger than its
# prose, and letting it through as text would dump CSS into the reader.
_CLEAN_CONTENT_TAGS = {'noscript', 'script', 'style', 'head', 'title'}

_SAFE_SCHEME = re.compile(r'^https?://', re.IGNORECASE)


def _localize_images(
    html: str, inline_images: dict[str, str] | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Point every http(s) img at a local path. Returns (html, [(hash, url)]).

    Known `cid:` MIME references are mapped to their already-stored local
    files. Anything else not plainly http(s) — `javascript:`, `data:`, a bare
    fragment — is dropped rather than rewritten. It has to be filtered *here* because nh3
    validates url_schemes only for attributes it knows are URLs; a data-src it
    has never heard of is just a string to it, and the frontend promotes that
    string to a real src when the reader clicks Load images.

    The returned pairs are what the caller queues for download. They are
    deduplicated within the message — a signature logo repeated in a quoted
    reply chain is one fetch, not six.
    """
    soup = BeautifulSoup(html, 'html.parser')
    inline_images = {k.lower(): v for k, v in (inline_images or {}).items()}
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
        elif src.lower().startswith('cid:'):
            local = inline_images.get(src[4:].strip().strip('<>').lower())
            if local:
                img['data-src'] = local
    return str(soup), list(found.items())


def sanitize_email_html(
    html: str, inline_images: dict[str, str] | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Untrusted email HTML -> (safe markup, images to fetch).

    Returns the image list rather than queueing it here so this stays a pure
    function with no DB or network, testable on a fixture string — the same
    line backend/fanfic/xenforo.py draws.
    """
    if not html or not html.strip():
        return '', []
    localized, images = _localize_images(html, inline_images)
    clean = nh3.clean(
        localized,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        filter_style_properties=_ALLOWED_STYLE_PROPERTIES,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        url_schemes={'http', 'https', 'mailto'},
        link_rel='noopener noreferrer nofollow',
    )
    return clean, images
