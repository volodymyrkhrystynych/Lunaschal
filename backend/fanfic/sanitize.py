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


def sanitize_chapter_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes={'http', 'https'},
        link_rel='noopener noreferrer',
    )


def html_to_text(html: str) -> str:
    text = BeautifulSoup(html, 'html.parser').get_text(' ')
    return re.sub(r'[ \t]*\n[ \t\n]*', '\n', re.sub(r'[ \t]+', ' ', text)).strip()


def count_words(text: str) -> int:
    return len(text.split())
