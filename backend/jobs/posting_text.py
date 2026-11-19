"""A posting's body as Markdown, for the phone's swipe card.

`jobs.description` is the wrong thing to show a person: `strip_html` puts every
text node on its own line, so "We use <b>Swift</b> daily" is stored as three
lines and a bulleted list loses its bullets. It is what triage and matching
read, and they don't care. The card does, so the shape is rebuilt here from
`jobs.raw` — the adapter's payload, kept verbatim — at read time, which also
means every posting already stored gets it with no backfill.

The HTML is picked by the payload's shape rather than by `jobs.source`: a
Workday row and a Lever row are told apart by what they carry. Adzuna sends
only a snippet and no HTML, and a manual posting has no raw at all; both fall
back to the stored text, one paragraph per line.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from html.parser import HTMLParser

_SKIP = {'script', 'style', 'noscript', 'svg', 'head', 'title'}
_BLOCK = {'p', 'div', 'section', 'article', 'header', 'footer', 'table', 'tr', 'blockquote'}
_HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
# Between two lists Markdown needs a blank line, or they read as one.
_LIST_END = '\0'


def source_html(raw) -> str | None:
    """The posting body's HTML out of an adapter's raw payload, if it had any."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    detail = raw.get('detail')
    if isinstance(detail, dict):  # Workday
        info = detail.get('jobPostingInfo') or {}
        return info.get('jobDescription') or None
    if raw.get('descriptionHtml'):  # Ashby
        return raw['descriptionHtml']
    if 'lists' in raw or 'descriptionPlain' in raw:  # Lever
        parts = [raw.get('description') or '']
        for section in raw.get('lists') or []:
            if isinstance(section, dict) and section.get('content'):
                parts.append(f"<h3>{html_mod.escape(section.get('text') or '')}</h3>"
                             f"<ul>{section['content']}</ul>")
        parts.append(raw.get('additional') or '')
        joined = ''.join(parts)
        return joined or None
    if raw.get('content'):  # Greenhouse sends it entity-escaped
        return html_mod.unescape(raw['content'])
    return None


class _MarkdownWriter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.line: list[str] = []
        self.prefix = ''
        self.skip = 0
        self.lists: list[str] = []
        self.counters: list[int] = []
        self.href: str | None = None

    def _flush(self):
        text = re.sub(r'\s+', ' ', ''.join(self.line)).strip()
        if text:
            self.blocks.append(self.prefix + text)
        self.line = []
        self.prefix = ''

    def _open(self, tag):
        self.line.append('**' if tag in ('strong', 'b') else '*')

    def _close(self, tag):
        marker = '**' if tag in ('strong', 'b') else '*'
        if marker not in self.line:
            return  # opened before a block break flushed the line
        start = len(self.line) - 1 - self.line[::-1].index(marker)
        inner = ''.join(self.line[start + 1:])
        if not inner.strip():
            # Emphasis around nothing but spaces would read as stray asterisks.
            del self.line[start]
            return
        # Markdown wants the markers hugging the words: "** Swift **" isn't bold.
        lead = inner[:len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        self.line[start + 1:] = [inner.strip()]
        self.line[start:start] = [lead]
        self.line.append(marker + trail)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in _BLOCK or tag in _HEADINGS:
            self._flush()
            if tag in _HEADINGS:
                self.prefix = '### '
        elif tag in ('ul', 'ol'):
            self._flush()
            self.lists.append(tag)
            self.counters.append(0)
        elif tag == 'li':
            self._flush()
            depth = '  ' * max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1] == 'ol':
                self.counters[-1] += 1
                self.prefix = f'{depth}{self.counters[-1]}. '
            else:
                self.prefix = f'{depth}- '
        elif tag == 'br':
            self._flush()
        elif tag in ('strong', 'b', 'em', 'i'):
            self._open(tag)
        elif tag == 'a':
            self.href = dict(attrs).get('href')
            self.line.append('[')

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(self.skip - 1, 0)
            return
        if self.skip:
            return
        if tag in _BLOCK or tag in _HEADINGS or tag == 'li':
            self._flush()
        elif tag in ('ul', 'ol'):
            self._flush()
            if self.lists:
                self.lists.pop()
                self.counters.pop()
            if not self.lists:
                self.blocks.append(_LIST_END)
        elif tag in ('strong', 'b', 'em', 'i'):
            self._close(tag)
        elif tag == 'a':
            href = self.href or ''
            self.href = None
            if href.startswith(('http://', 'https://', 'mailto:')):
                self.line.append(f']({href})')
            else:
                # Not a link the phone can follow; keep the words, drop the bracket.
                start = len(self.line) - 1 - self.line[::-1].index('[')
                del self.line[start]

    def handle_data(self, data):
        if not self.skip:
            # Markdown's own characters in the posting's text stay text.
            self.line.append(re.sub(r'([\\`*_\[\]])', r'\\\1', data))


def html_to_markdown(source: str) -> str:
    writer = _MarkdownWriter()
    writer.feed(source)
    writer.close()
    writer._flush()
    blocks: list[str] = []
    for block in writer.blocks:
        if block == _LIST_END:
            blocks.append(block)
            continue
        # A heading that only repeats the bold paragraph style is still a
        # heading: postings use <p><strong>About you</strong></p> as often as <h3>.
        # "<h3><strong>About</strong></h3>" is already a heading; drop the bold.
        block = re.sub(r'^### \*\*(.+)\*\*$', r'### \1', block)
        bare = re.fullmatch(r'\*\*(.+?)\*\*:?', block)
        if bare and len(bare.group(1)) <= 80:
            block = f"### {bare.group(1).rstrip(':').strip()}"
        blocks.append(block)
    # List items sit together; everything else is separated by a blank line.
    out: list[str] = []
    prev_item = False
    for block in blocks:
        if block == _LIST_END:
            prev_item = False
            continue
        is_item = bool(re.match(r'\s*(-|\d+\.) ', block))
        if out:
            out.append('\n' if is_item and prev_item else '\n\n')
        out.append(block)
        prev_item = is_item
    return ''.join(out)


def posting_markdown(raw, description: str) -> str:
    """The body to show: rebuilt from the source HTML, else the stored text."""
    html = source_html(raw)
    if html:
        text = html_to_markdown(html)
        if text:
            return text
    lines = [re.sub(r'([\\`*_\[\]])', r'\\\1', line.strip())
             for line in (description or '').splitlines()]
    return '\n\n'.join(line for line in lines if line)
