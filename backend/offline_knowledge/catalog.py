"""The Kiwix catalogue: what is available to download, and where to get it.

Five facts about this service shape every function here, and none of them are
guessable from its name:

* **`library.kiwix.org/catalog/v2/…` 301s to `opds.library.kiwix.org`.** Follow
  redirects or get an empty body.
* **It is Atom/OPDS XML, not JSON**, despite the "v2". Parsed with the stdlib
  `xml.etree.ElementTree` rather than BeautifulSoup, which is the HTML tool.
* **The acquisition link is a `.meta4` (Metalink 4), not the `.zim`.** Fetching
  it yields the authoritative size, three checksums, a piece list and a
  priority-ordered mirror list. Its `<size>` and the OPDS link's `length`
  attribute *disagree* -- 361379 against 361472 on the entry this was verified
  against -- and the meta4 is the one the mirrors will actually serve, so it
  wins. Downloading the `href` directly would fetch a 2 KB XML file named
  `.zim`.
* **`/entry/<uuid>` is not usable and is deliberately not called.** It answers
  200 with a bare `<entry>` root and no feed around it, and that document uses
  the `dc:` prefix without declaring it anywhere -- it is not well-formed XML,
  and no conforming parser will read it. One archive is therefore re-resolved
  through `/entries?name=<slug>`, which is a proper feed; the slug is not
  unique on its own (`wikipedia_en_all` matches three flavours), so the uuid
  picks between them. `parse_entries` still accepts a lone `<entry>` root, for
  the day that endpoint is fixed.
* **`q` matches title words, not slugs.** `q=stackoverflow` returns nothing;
  `q=Stack Overflow` returns four. The UI says so, because a search box that
  silently fails on the name printed on the file is worse than no search box.

`lang=` takes ISO-639-3 (`eng`, `fra`) and does narrow -- an earlier note in
this project's design doc said it did not, from testing it against
`category=stack_exchange`, all 181 entries of which happen to be English.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from backend.offline_knowledge import kinds

CATALOG_BASE = 'https://opds.library.kiwix.org/catalog/v2'
TIMEOUT = 20
# The catalogue holds ~3630 entries. A page larger than this is a UI nobody
# scrolls and a response nobody wanted to pay for.
MAX_COUNT = 100
DEFAULT_COUNT = 24

ACQUISITION_REL = 'http://opds-spec.org/acquisition/open-access'

# Filters the catalogue honours. Anything else is dropped rather than passed
# through: this is a proxied query string, and forwarding arbitrary parameters
# to an upstream service is how a proxy becomes an open one.
ALLOWED_FILTERS = frozenset(
    {'q', 'category', 'tag', 'lang', 'name', 'flavour', 'count', 'start'}
)

_UUID_RE = re.compile(r'^[0-9a-fA-F][0-9a-fA-F-]{7,63}$')


class CatalogUnavailable(RuntimeError):
    """The catalogue could not be reached or did not answer with XML."""


def _parse(xml: str, what: str, *, roots: tuple[str, ...] = ()):
    """Parse, and strip every namespace so one set of tag names works.

    The catalogue mixes four namespaces across its feeds and uses none at all
    on `/entry/<uuid>`; the local names it uses (`title`, `language`, `issued`,
    `count`, `size`, `hash`) do not collide, so flattening them loses nothing
    and removes the branch that would otherwise exist in every lookup.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CatalogUnavailable(f'{what} is not valid XML: {exc}') from exc
    for node in root.iter():
        if isinstance(node.tag, str) and node.tag.startswith('{'):
            node.tag = node.tag.partition('}')[2]
    if roots and root.tag not in roots:
        # A proxy or an outage can answer 200 with an HTML error page, and
        # `<html>503</html>` is perfectly well-formed XML -- so without this
        # the catalogue would read as simply empty, and the UI would say the
        # library has nothing in it rather than that it could not be reached.
        raise CatalogUnavailable(
            f'{what} was a <{root.tag}> document, not a catalogue feed')
    return root


def _text(node, tag: str, default: str = '') -> str:
    if node is None:
        return default
    found = node.find(tag)
    return (found.text or '').strip() if found is not None and found.text else default


def _int(node, tag: str) -> int | None:
    try:
        return int(_text(node, tag))
    except (TypeError, ValueError):
        return None


def _entry(node) -> dict:
    """One catalogue entry, labelled by the same code the registry uses.

    `kind` and `ftindex` go through `kinds` rather than being re-derived here,
    so an archive reads identically before and after it is installed -- a row
    that says "Documentation / title search only" in the catalogue must not
    turn into something else once it is on the drive.
    """
    name = _text(node, 'name')
    tags = _text(node, 'tags')
    flags, _bare = kinds.parse_tags(tags)
    creator = _text(node.find('author'), 'name')
    kind, _terms = kinds.classify({'Tags': tags, 'Name': name, 'Creator': creator}, name)

    url = length = ''
    for link in node.findall('link'):
        if link.get('rel') == ACQUISITION_REL:
            url = link.get('href') or ''
            length = link.get('length') or ''
            break

    return {
        'uuid': _text(node, 'id').removeprefix('urn:uuid:'),
        'name': name,
        'title': _text(node, 'title'),
        'summary': _text(node, 'summary'),
        'language': _text(node, 'language'),
        'flavour': _text(node, 'flavour'),
        'category': _text(node, 'category'),
        'creator': creator,
        'tags': tags,
        'kind': kind,
        # **The catalogue's claim, and it is not always true.** The 231
        # DevDocs entries are all tagged `_ftindex:no`, but the archives
        # themselves are not: `devdocs_en_sinon_2026-08.zim` and
        # `devdocs_en_qunit_2026-07.zim` both report `has_fulltext_index ==
        # True` from libzim and carry no `_ftindex` tag of their own at all
        # (their whole `Tags` is `devdocs;sinon`). The flag lives only in the
        # library server's generated metadata. So this is shown as what the
        # catalogue says, never as a fact -- the truth is only knowable once
        # the file is on disk, which is what `registry._probe` reads.
        'ftindex': flags.get('ftindex', 'yes') != 'no',
        'articleCount': _int(node, 'articleCount'),
        'mediaCount': _int(node, 'mediaCount'),
        'issued': _text(node, 'issued'),
        'meta4Url': url,
        # Advisory only: parse_meta4's <size> is what a download trusts.
        'approxSize': int(length) if length.isdigit() else None,
    }


def parse_entries(xml: str) -> tuple[list[dict], int]:
    """Parse an acquisition feed -- or a lone entry -- into entries and a total."""
    root = _parse(xml, 'Catalogue response', roots=('feed', 'entry'))
    if root.tag == 'entry':
        return [_entry(root)], 1
    entries = [_entry(node) for node in root.findall('entry')]
    total = _int(root, 'totalResults')
    return entries, total if total is not None else len(entries)


def parse_navigation(xml: str) -> list[dict]:
    """Parse a navigation feed (categories, languages) into pickable options.

    The two feeds are not the same shape -- a language entry carries
    `dc:language` and `thr:count`, a category entry carries neither -- so both
    are optional here rather than being two near-identical parsers.
    """
    root = _parse(xml, 'Catalogue response', roots=('feed',))
    out = []
    for node in root.findall('entry'):
        label = _text(node, 'title')
        if not label:
            continue
        out.append({
            'label': label,
            'code': _text(node, 'language') or label,
            'count': _int(node, 'count'),
        })
    return out


def parse_meta4(xml: str) -> dict:
    """Parse a Metalink 4 acquisition file.

    Returns the authoritative size, whatever hashes were offered, the piece
    list (sha-1 over fixed-length pieces, which is what makes a resumed
    transfer checkable before its last byte), and the mirrors in the order the
    server ranked them -- `priority` ascending, nearest first.
    """
    node = _parse(xml, 'Mirror list', roots=('metalink',)).find('file')
    if node is None:
        raise CatalogUnavailable('Mirror list contained no file')

    hashes = {}
    for entry in node.findall('hash'):
        if entry.text:
            hashes[(entry.get('type') or '').lower().replace('-', '')] = entry.text.strip().lower()

    piece_length, pieces = None, []
    piece_node = node.find('pieces')
    if piece_node is not None:
        # Only sha-1 pieces are understood. Anything else is ignored rather
        # than misread -- the whole-file hash still gates the result, the
        # transfer just loses its mid-flight checkpoint.
        if (piece_node.get('type') or '').lower().replace('-', '') == 'sha1':
            try:
                piece_length = int(piece_node.get('length') or 0) or None
            except ValueError:
                piece_length = None
            if piece_length:
                pieces = [h.text.strip().lower()
                          for h in piece_node.findall('hash') if h.text]

    mirrors = []
    for url in node.findall('url'):
        if not (url.text or '').strip():
            continue
        try:
            priority = int(url.get('priority') or 999)
        except ValueError:
            priority = 999
        mirrors.append((priority, url.text.strip()))
    mirrors.sort(key=lambda pair: pair[0])

    return {
        'filename': node.get('name') or '',
        'size': _int(node, 'size'),
        'hashes': hashes,
        'sha256': hashes.get('sha256'),
        'md5': hashes.get('md5'),
        'pieceLength': piece_length if pieces else None,
        'pieces': pieces if piece_length else [],
        'mirrors': [url for _priority, url in mirrors],
    }


def _get(url: str, params: dict | None = None) -> str:
    try:
        resp = requests.get(
            url, params=params, timeout=TIMEOUT, allow_redirects=True,
            headers={'Accept': 'application/atom+xml, application/xml, */*'},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise CatalogUnavailable(f'Could not reach the Kiwix catalogue: {exc}') from exc
    return resp.text


def fetch_entries(**filters) -> tuple[list[dict], int]:
    params = {k: v for k, v in filters.items()
              if k in ALLOWED_FILTERS and v not in (None, '')}
    try:
        params['count'] = max(1, min(int(params.get('count', DEFAULT_COUNT)), MAX_COUNT))
    except (TypeError, ValueError):
        params['count'] = DEFAULT_COUNT
    return parse_entries(_get(f'{CATALOG_BASE}/entries', params))


def fetch_entry(name: str, uuid: str = '') -> dict | None:
    """Re-resolve one archive from the catalogue, by slug and then by uuid.

    Through `/entries?name=`, never `/entry/<uuid>` -- see the module docstring
    for why that endpoint cannot be parsed. This exists so that queueing a
    download re-reads the entry server-side instead of trusting the mirror URL
    and size a browser handed back.
    """
    if not name:
        return None
    entries, _total = fetch_entries(name=name, count=MAX_COUNT)
    if uuid and _UUID_RE.match(uuid):
        for entry in entries:
            if entry['uuid'] == uuid:
                return entry
        return None
    return entries[0] if len(entries) == 1 else None


def fetch_meta4(url: str) -> dict:
    return parse_meta4(_get(url))


def fetch_facets() -> dict:
    return {
        'categories': parse_navigation(_get(f'{CATALOG_BASE}/categories')),
        'languages': parse_navigation(_get(f'{CATALOG_BASE}/languages')),
    }
