"""Import a Fandom (MediaWiki) wiki's XML dump as a searchable canon source.

The input is what a wiki's `Special:Statistics` page links to — a
`pages_current.xml.7z` (or `.gz`) export of every page's latest revision — or
the same file from a WikiTeam mirror, or a plain `.xml`/`.bz2`. The output is
one SQLite file in the docpack format (`meta`, `chunks`, `chunks_fts`) under
`CANON_ROOT`, searched by `backend/writing/canon_tools.py` through the same
`docpacks.search_file` / `read_file` the Knowledge tab uses.

Things that are not obvious:

- **The dump is streamed, never loaded.** A large fandom's current-pages export
  is a few GB of XML. `iterparse` plus clearing each finished `<page>` (and the
  root's reference to it) keeps memory flat however big the wiki is.
- **A `<!DOCTYPE` is refused outright.** A MediaWiki export never has one, and
  a document that does is the only way to smuggle entity expansion into a
  parser. Cheaper and clearer than a hardened parser dependency.
- **Infoboxes are kept, as `key: value` lines.** On a fandom wiki the infobox is
  where the canon facts live — age, affiliation, first appearance — and a
  plain wikitext strip deletes every template, infobox included.
- **Redirects become aliases.** "Harry" redirects to "Harry Potter"; a row
  titled "Harry" filed under the target's page means a search for the short
  name lands on, and reads, the real article.
- **Only the main namespace (0) is kept.** Talk pages, user pages, templates
  and categories are noise to a story's canon.
- **It runs on its own thread, not the `llm_jobs` lane.** There is no model call
  in it; queuing an hour of XML parsing behind (or ahead of) model work would
  hold one up for no reason. Progress is in memory, and a restart marks an
  in-flight row `error` (`_reset_stale_canon_imports`) rather than pretending.
"""
import bz2
import gzip
import io
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from backend.db.connection import get_db
from backend.offline_knowledge import docpacks
from backend.writing.canon_tools import canon_root

logger = logging.getLogger(__name__)

# Large enough for any fandom's current-pages dump; small enough that a wrong
# URL (a full-history dump, a video) is refused rather than filling the disk.
MAX_DOWNLOAD_BYTES = 4 * 1024 ** 3
FETCH_TIMEOUT = 60
MAX_REDIRECTS = 5

# One chunk is one section, or a piece of a long one. About the size of a
# docpack section, so BM25 scores and read windows behave the same.
MAX_CHUNK_CHARS = 2400

SCHEMA = '''
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  doc_path TEXT NOT NULL,
  doc_title TEXT NOT NULL,
  section_title TEXT NOT NULL,
  content TEXT NOT NULL,
  tokens INTEGER NOT NULL,
  has_code INTEGER DEFAULT 0
);
CREATE INDEX chunks_doc ON chunks(doc_path);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  doc_title, section_title, content,
  content='chunks', content_rowid='id',
  tokenize='porter unicode61'
);
'''


class CanonImportError(ValueError):
    """A dump that cannot be imported. The message is shown to the user."""


_progress: dict[str, dict] = {}
_progress_lock = threading.Lock()
_active: set[threading.Event] = set()


def progress(source_id: str) -> dict | None:
    with _progress_lock:
        p = _progress.get(source_id)
        return dict(p) if p else None


def _set_progress(source_id: str, **values) -> None:
    with _progress_lock:
        _progress.setdefault(source_id, {}).update(values)


def wait_idle(timeout: float = 30.0) -> bool:
    """Tests only: block until every started import has finished."""
    deadline = time.monotonic() + timeout
    for event in list(_active):
        if not event.wait(max(0.0, deadline - time.monotonic())):
            return False
    return True


# --- opening the dump --------------------------------------------------------

_SEVEN_Z = b"7z\xbc\xaf\x27\x1c"


class _Prefixed(io.RawIOBase):
    """A stream with some already-read bytes put back in front of it."""

    def __init__(self, head: bytes, rest):
        self._head, self._rest = head, rest

    def readable(self):
        return True

    def readinto(self, buffer):
        if self._head:
            n = min(len(buffer), len(self._head))
            buffer[:n] = self._head[:n]
            self._head = self._head[n:]
            return n
        data = self._rest.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def _extract_7z(path: Path, workdir: Path) -> Path:
    try:
        import py7zr
    except ImportError as e:  # pragma: no cover - dependency is in requirements.txt
        raise CanonImportError(
            '.7z dumps need the py7zr package (pip install py7zr), or unpack '
            'the dump yourself and upload the .xml') from e
    with py7zr.SevenZipFile(path, 'r') as archive:
        names = [n for n in archive.getnames() if n.lower().endswith('.xml')]
        if not names:
            raise CanonImportError('That .7z archive has no .xml file in it')
        archive.extract(path=workdir, targets=[names[0]])
    return workdir / names[0]


def open_dump(path: Path, workdir: Path):
    """A binary stream of the dump's XML, whatever it was packed in."""
    with open(path, 'rb') as fh:
        magic = fh.read(6)
    if magic.startswith(_SEVEN_Z):
        raw = open(_extract_7z(path, workdir), 'rb')
    elif magic.startswith(b'\x1f\x8b'):
        raw = gzip.open(path, 'rb')
    elif magic.startswith(b'BZh'):
        raw = bz2.open(path, 'rb')
    else:
        raw = open(path, 'rb')
    head = raw.read(16384)
    if b'<!DOCTYPE' in head or b'<!ENTITY' in head:
        raw.close()
        raise CanonImportError('Refusing an XML file with a DOCTYPE — a MediaWiki '
                               'export never has one')
    if b'<mediawiki' not in head:
        raw.close()
        raise CanonImportError('That is not a MediaWiki XML export')
    return io.BufferedReader(_Prefixed(head, raw), buffer_size=1 << 20), raw


# --- wikitext ----------------------------------------------------------------

_SKIP_LINK = re.compile(r'^\s*:?\s*(category|file|image|media)\s*:', re.I)
_HEADING = re.compile(r'^(={2,6})\s*(.+?)\s*\1\s*$', re.M)
_REF = re.compile(r'<ref[^>]*/>|<ref[^>]*>.*?</ref>', re.S | re.I)
_COMMENT = re.compile(r'<!--.*?-->', re.S)
_GALLERY = re.compile(r'<gallery[^>]*>.*?</gallery>', re.S | re.I)
_BLANKS = re.compile(r'\n{3,}')


def _is_infobox(template) -> bool:
    name = str(template.name).strip().lower()
    if 'infobox' in name or 'character' in name:
        return True
    named = [p for p in template.params if p.showkey]
    return len(named) >= 4


def _infobox_lines(template, mw) -> list[str]:
    lines = []
    for param in template.params:
        if not param.showkey:
            continue
        key = str(param.name).strip()
        if key.lower() in ('image', 'caption', 'imagecaption', 'gallery', 'width'):
            continue
        value = mw.parse(str(param.value)).strip_code(normalize=True, collapse=True)
        value = ' '.join(value.split())
        if value:
            lines.append(f'{key.replace("_", " ")}: {value}')
    return lines


def wikitext_to_text(wikitext: str) -> str:
    """Readable prose from a page's wikitext, infobox facts first."""
    import mwparserfromhell as mw

    wikitext = _GALLERY.sub('', _REF.sub('', _COMMENT.sub('', wikitext or '')))
    code = mw.parse(wikitext)
    facts: list[str] = []
    for template in code.filter_templates(recursive=False):
        if _is_infobox(template):
            facts.extend(_infobox_lines(template, mw))
        try:
            code.remove(template)
        except ValueError:
            pass
    for link in code.filter_wikilinks():
        if _SKIP_LINK.match(str(link.title)):
            try:
                code.remove(link)
            except ValueError:
                pass
    text = code.strip_code(normalize=True, collapse=True)
    text = _BLANKS.sub('\n\n', text).strip()
    if facts:
        text = '\n'.join(facts) + ('\n\n' + text if text else '')
    return text


def split_sections(wikitext: str) -> list[tuple[str, str]]:
    """(heading, wikitext) per section, the lead under an empty heading.
    Deeper headings stay inside their level-2 section, kept as a line."""
    out: list[tuple[str, str]] = []
    current, buf = '', []
    for line in (wikitext or '').splitlines():
        m = _HEADING.match(line)
        if m and len(m.group(1)) == 2:
            out.append((current, '\n'.join(buf)))
            current, buf = m.group(2).strip(), []
        elif m:
            buf.append(m.group(2).strip())
        else:
            buf.append(line)
    out.append((current, '\n'.join(buf)))
    return [(h, body) for h, body in out if body.strip() or h]


def _pieces(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    pieces, buf = [], ''
    for para in text.split('\n\n'):
        while len(para) > MAX_CHUNK_CHARS:
            if buf:
                pieces.append(buf)
                buf = ''
            pieces.append(para[:MAX_CHUNK_CHARS])
            para = para[MAX_CHUNK_CHARS:]
        if buf and len(buf) + len(para) + 2 > MAX_CHUNK_CHARS:
            pieces.append(buf)
            buf = para
        else:
            buf = f'{buf}\n\n{para}' if buf else para
    if buf:
        pieces.append(buf)
    return pieces


def page_chunks(title: str, wikitext: str) -> list[tuple[str, str]]:
    """(section_title, text) rows for one article."""
    rows = []
    for heading, body in split_sections(wikitext):
        text = wikitext_to_text(body)
        if not text:
            continue
        for piece in _pieces(text):
            rows.append((heading, piece))
    return rows


# --- the dump ----------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def iter_pages(stream):
    """Yields ('site', {name, base}) once, then ('page', {title, ns, redirect,
    text}) for every page, clearing each element as it goes."""
    root = None
    site_sent = False
    for event, elem in ET.iterparse(stream, events=('start', 'end')):
        tag = _local(elem.tag)
        if event == 'start':
            if root is None:
                root = elem
            continue
        if tag == 'siteinfo' and not site_sent:
            site_sent = True
            info = {_local(c.tag): (c.text or '') for c in elem}
            yield 'site', {'name': info.get('sitename', ''), 'base': info.get('base', '')}
            elem.clear()
        elif tag == 'page':
            page = {'title': '', 'ns': '0', 'redirect': None, 'text': ''}
            for child in elem:
                ctag = _local(child.tag)
                if ctag == 'title':
                    page['title'] = child.text or ''
                elif ctag == 'ns':
                    page['ns'] = (child.text or '0').strip()
                elif ctag == 'redirect':
                    page['redirect'] = child.get('title')
                elif ctag == 'revision':
                    # The last <revision> wins: a current-pages dump has one,
                    # a full-history dump has them oldest first.
                    for rc in child:
                        if _local(rc.tag) == 'text':
                            page['text'] = rc.text or ''
            yield 'page', page
            elem.clear()
            if root is not None:
                # The root still holds every cleared <page>; dropping them is
                # what keeps a multi-GB dump at flat memory.
                root.clear()


def build(source_id: str, dump_path: Path, *, name: str = '',
          source_url: str = '') -> dict:
    """Parse `dump_path` into `<CANON_ROOT>/<source_id>.sqlite`. Returns
    {filename, pages, chunks, name, wiki_host}. Raises CanonImportError."""
    root = canon_root()
    root.mkdir(parents=True, exist_ok=True)
    part = root / f'.{source_id}.part'
    part.unlink(missing_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix='canon-', dir=root))
    stream = raw = None
    conn = sqlite3.connect(part)
    try:
        conn.executescript(SCHEMA)
        stream, raw = open_dump(dump_path, workdir)
        site = {'name': '', 'base': ''}
        pages = chunks = 0
        batch: list[tuple] = []

        def flush():
            conn.executemany(
                'INSERT INTO chunks (doc_path, doc_title, section_title, content, tokens)'
                ' VALUES (?,?,?,?,?)', batch)
            batch.clear()

        for kind, item in iter_pages(stream):
            if kind == 'site':
                site = item
                continue
            if item['ns'] != '0' or not item['title']:
                continue
            title = item['title']
            text = item['text'] or ''
            target = item['redirect']
            if not target and text.lstrip().upper().startswith('#REDIRECT'):
                m = re.search(r'\[\[([^\]|#]+)', text)
                target = m.group(1).strip() if m else None
            if target:
                batch.append((target, title, '', f'{title} — another name for {target}.', 8))
                chunks += 1
            else:
                rows = page_chunks(title, text)
                if not rows:
                    continue
                pages += 1
                for heading, content in rows:
                    batch.append((title, title, heading, content, len(content) // 4))
                    chunks += 1
            if len(batch) >= 500:
                flush()
                _set_progress(source_id, pages=pages, chunks=chunks)
        flush()
        if not chunks:
            raise CanonImportError('The dump had no articles in it')

        host = urlparse(site['base']).hostname or ''
        label = name or site['name'] or host or 'Canon wiki'
        conn.executemany('INSERT INTO meta (key, value) VALUES (?, ?)', [
            ('name', label), ('version', time.strftime('%Y-%m-%d')),
            ('description', f'{label} — imported from a MediaWiki XML dump'),
            ('source_url', source_url or site['base']),
            ('licence', 'CC BY-SA 3.0'),
        ])
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()
        conn = None
        try:
            docpacks.validate(part)
        except docpacks.DocPackError as e:
            raise CanonImportError(f'The import produced an unreadable file: {e}') from e
        filename = f'{source_id}.sqlite'
        os.replace(part, root / filename)
        return {'filename': filename, 'pages': pages, 'chunks': chunks,
                'name': label, 'wiki_host': host}
    except ET.ParseError as e:
        raise CanonImportError(f'The XML could not be parsed: {e}') from e
    finally:
        if conn is not None:
            conn.close()
        for fh in (stream, raw):
            try:
                if fh is not None:
                    fh.close()
            except Exception:
                pass
        part.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)


# --- fetching ----------------------------------------------------------------

_DUMP_SUFFIXES = ('.xml', '.xml.gz', '.xml.7z', '.xml.bz2', '.7z', '.gz', '.bz2')
_DUMP_LINK = re.compile(r'''https?://[^\s"'<>]+?pages_current\.xml\.(?:7z|gz|bz2)''')


def _get(url: str, *, stream: bool = False):
    """GET with the SSRF guard re-run on every redirect hop."""
    from backend.research.web import UnsafeUrl, assert_public_url

    for _ in range(MAX_REDIRECTS + 1):
        try:
            assert_public_url(url)
        except UnsafeUrl as e:
            raise CanonImportError(str(e)) from e
        resp = requests.get(url, stream=stream, timeout=FETCH_TIMEOUT,
                            allow_redirects=False,
                            headers={'User-Agent': 'Lunaschal canon import'})
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get('Location')
            resp.close()
            if not location:
                raise CanonImportError('A redirect with nowhere to go')
            url = urljoin(url, location)
            continue
        if resp.status_code != 200:
            resp.close()
            raise CanonImportError(f'{url} answered {resp.status_code}')
        return resp, url
    raise CanonImportError('Too many redirects')


def resolve_dump_url(url: str) -> str:
    """A dump file URL as-is; a Fandom wiki URL becomes the current-pages dump
    its Special:Statistics page links to."""
    url = (url or '').strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise CanonImportError('Paste an http(s) link to a wiki or to its dump')
    if parsed.path.lower().endswith(_DUMP_SUFFIXES):
        return url
    stats = f'{parsed.scheme}://{parsed.hostname}/wiki/Special:Statistics'
    resp, _ = _get(stats)
    try:
        html = resp.text
    finally:
        resp.close()
    match = _DUMP_LINK.search(html)
    if not match:
        raise CanonImportError(
            'That wiki has no current-pages dump on its Special:Statistics page. '
            'Fandom makes them on request — or find one on archive.org (WikiTeam) '
            'and paste that link')
    return match.group(0)


def download(url: str, dest: Path) -> Path:
    resp, final = _get(url, stream=True)
    written = 0
    try:
        with open(dest, 'wb') as out:
            for chunk in resp.iter_content(1 << 20):
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise CanonImportError('That file is too large to be a pages dump')
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        resp.close()
    return dest


# --- the worker --------------------------------------------------------------

def run(source_id: str, *, path: Path | None = None, url: str | None = None,
        name: str = '') -> None:
    """Fetch (when given a URL) and build one source, recording the outcome on
    its row. Never raises: this is the body of a daemon thread."""
    db = get_db()
    tmp = None
    try:
        if url:
            _set_progress(source_id, stage='downloading')
            dump_url = resolve_dump_url(url)
            root = canon_root()
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f'.{source_id}.download'
            path = download(dump_url, tmp)
            db.execute('UPDATE writing_canon_sources SET source_url=? WHERE id=?',
                       (dump_url, source_id))
            db.commit()
        _set_progress(source_id, stage='parsing', pages=0, chunks=0)
        result = build(source_id, path, name=name, source_url=url or '')
        db.execute(
            "UPDATE writing_canon_sources SET status='ready', error=NULL, filename=?,"
            ' pages=?, chunks=?, name=?, wiki_host=COALESCE(NULLIF(?, \'\'), wiki_host),'
            ' updated_at=? WHERE id=?',
            (result['filename'], result['pages'], result['chunks'], result['name'],
             result['wiki_host'], int(time.time()), source_id),
        )
        db.commit()
    except Exception as e:
        message = str(e) if isinstance(e, CanonImportError) else f'Import failed: {e}'
        logger.warning('Canon import %s failed: %s', source_id, e)
        db.execute(
            "UPDATE writing_canon_sources SET status='error', error=?, updated_at=? WHERE id=?",
            (message[:500], int(time.time()), source_id),
        )
        db.commit()
    finally:
        with _progress_lock:
            _progress.pop(source_id, None)
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        elif path is not None and path.name.startswith('.') and path.parent == canon_root():
            # An uploaded dump staged by the route; the import is its only use.
            path.unlink(missing_ok=True)


def start(source_id: str, **kwargs) -> None:
    done = threading.Event()
    _active.add(done)

    def target():
        try:
            run(source_id, **kwargs)
        finally:
            done.set()
            _active.discard(done)

    threading.Thread(target=target, daemon=True, name=f'canon-import-{source_id}').start()
