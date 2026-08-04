"""HTML → readable text.

Extracted from backend/routes/cookbook.py so the recipe importer and the Ideas
research agent share one implementation rather than growing two that drift.
"""
import re
from html.parser import HTMLParser

DEFAULT_MAX_CHARS = 20000


class TextExtractor(HTMLParser):
    _SKIP = {'script', 'style', 'noscript', 'svg', 'head'}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == 'title':
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        # <title> lives inside <head>, which is skipped for body text — but the
        # title itself is worth keeping, so it is captured separately.
        if self._in_title and self.title is None and data.strip():
            self.title = data.strip()[:300]
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def strip_html(html: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    parser = TextExtractor()
    parser.feed(html)
    text = '\n'.join(parser.parts)
    return re.sub(r'\n{3,}', '\n\n', text)[:max_chars]


def strip_html_with_title(html: str, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, str | None]:
    """(text, title) — the title is what a citation is labelled with."""
    parser = TextExtractor()
    parser.feed(html)
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(parser.parts))[:max_chars]
    return text, parser.title
