"""Chapter HTML is sanitized once at import time; the frontend renders the
stored HTML as trusted content. Pipeline order matters: image srcs are
rewritten to /api/fanfic/... first, then sanitized — nh3 passes relative URLs
through (pinned by a unit test)."""

import re

import nh3
from bs4 import BeautifulSoup

_ALLOWED_TAGS = {
    'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'dd', 'div', 'dl', 'dt',
    'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins', 'li',
    'ol', 'p', 'pre', 's', 'small', 'span', 'strong', 'sub', 'sup', 'table',
    'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'u', 'ul',
}

_ALLOWED_ATTRS = {
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan'},
    'ol': {'start'},
}

# Dropped along with their children. XenForo wraps every inline image in a
# <noscript> fallback copy; per the HTML spec noscript children are raw text,
# so merely dropping the tag leaves the fallback <img> behind as escaped,
# visible markup. 'script'/'style' restate the ammonia default, which passing
# this argument at all would otherwise replace.
_CLEAN_CONTENT_TAGS = {'noscript', 'script', 'style'}


def sanitize_chapter_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        url_schemes={'http', 'https'},
        link_rel='noopener noreferrer',
    )


_ESCAPED_IMG = re.compile(r'&lt;img\b.*?&gt;', re.IGNORECASE | re.DOTALL)
_REAL_IMG_SRC = re.compile(r'<img\b[^>]*\bsrc="([^"]*)"', re.IGNORECASE)
_SRC_ATTR = re.compile(r'\bsrc="([^"]*)"', re.IGNORECASE)


def strip_escaped_image_fallbacks(html: str) -> str:
    """Repair chapters stored before <noscript> was dropped with its content.

    Only removes an escaped <img> whose src duplicates a real <img> in the same
    chapter, so markup quoted deliberately in prose survives.
    """
    real_srcs = set(_REAL_IMG_SRC.findall(html))
    if not real_srcs:
        return html

    def drop(match: re.Match[str]) -> str:
        src = _SRC_ATTR.search(match.group(0))
        return '' if src and src.group(1) in real_srcs else match.group(0)

    return _ESCAPED_IMG.sub(drop, html)


def html_to_text(html: str) -> str:
    text = BeautifulSoup(html, 'html.parser').get_text(' ')
    return re.sub(r'[ \t]*\n[ \t\n]*', '\n', re.sub(r'[ \t]+', ' ', text)).strip()


def count_words(text: str) -> int:
    return len(text.split())
