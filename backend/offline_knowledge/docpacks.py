"""Library documentation packages: a local Context7, read beside the ZIMs.

A docs package is one SQLite file per library version, in the format
neuledge/context publishes (https://github.com/neuledge/context): a `meta`
key/value table, a `chunks` table with one row per documentation section, and
an FTS5 index over it. Their community registry builds a hundred-odd libraries
daily from each project's own docs, so installing one is a single HTTP GET and
everything after that is offline -- which is the whole point. Reading their
format rather than building our own ingester means the scraping, chunking and
per-release versioning are somebody else's maintained pipeline, and the files
are portable: anything their CLI builds (`context add <git url>`) can be
uploaded here unchanged.

Two choices worth knowing:

* **Keyword search only.** BM25 over the package's own FTS5 index, built with
  the shared `fts_match_query` (prefix-OR, punctuation dropped). No embeddings,
  so a docs lookup never queues behind the model service.
* **The files live on the data disk, not the archive drive.** They are a few
  MB each, and keeping them under `./data/docpacks/` means a chat that cites
  one still resolves with the archive drive unplugged.

An uploaded or downloaded file is untrusted input: `validate` opens it
read-only with `trusted_schema=OFF` and checks the shape before a row is ever
written for it.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

import requests
from ulid import ULID

from backend.db.connection import fts_match_query, get_db

REGISTRY_URL = 'https://api.context.neuledge.com'
TIMEOUT = 30
# Package sizes run from under a megabyte to a few tens of MB (a language
# runtime's whole manual). Anything far past that is not a docs package.
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_ARTICLE_CHARS = 12_000
# Packages are small and SQLite releases the GIL, so a search can afford to
# open every installed one -- up to a point. Past this, the query has to name
# the library (its tokens intersect the package name) to be consulted.
MAX_PACKS_PER_SEARCH = 12
ID_PREFIX = 'docpack:'

_REGISTRY_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,40}$')
# npm scopes (`@trpc/server`) and Go module paths carry `@` and `/`.
_NAME_RE = re.compile(r'^[A-Za-z0-9@][A-Za-z0-9@/._+-]{0,200}$')
_VERSION_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,60}$')
_SPLIT = re.compile(r'[^a-z0-9]+')


class DocPackError(ValueError):
    """A package that is not one, or a request that cannot name one."""


class RegistryUnavailable(RuntimeError):
    pass


def docpacks_root() -> Path:
    return Path(os.environ.get('DOCPACKS_ROOT', './data/docpacks')).expanduser().resolve()


def public_id(pack_id: str) -> str:
    return f'{ID_PREFIX}{pack_id}'


def is_docpack_id(identifier: str) -> bool:
    return str(identifier or '').startswith(ID_PREFIX)


def _strip(identifier: str) -> str:
    return str(identifier)[len(ID_PREFIX):] if is_docpack_id(identifier) else str(identifier)


# --- the file format ---------------------------------------------------------

def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # An untrusted schema may not call functions with side effects from its
    # views or triggers; we only ever SELECT, but say so to SQLite too.
    conn.execute('PRAGMA trusted_schema=OFF')
    conn.execute('PRAGMA query_only=ON')
    return conn


def validate(path: Path) -> dict:
    """The package's meta and chunk count, or `DocPackError` if it is not one."""
    try:
        conn = _connect(path)
    except sqlite3.Error as exc:
        raise DocPackError(f'Not a SQLite file: {exc}') from exc
    try:
        try:
            objects = {r['name']: (r['type'], r['sql'] or '') for r in conn.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE name IN ('meta','chunks','chunks_fts')"
            )}
        except sqlite3.DatabaseError as exc:
            raise DocPackError(f'Not a SQLite file: {exc}') from exc
        if objects.get('meta', ('',))[0] != 'table' or objects.get('chunks', ('',))[0] != 'table':
            raise DocPackError('Not a docs package: missing the meta or chunks table')
        fts = objects.get('chunks_fts')
        if not fts or 'fts5' not in fts[1].lower():
            raise DocPackError('Not a docs package: missing the chunks_fts index')
        meta = {r['key']: r['value'] for r in conn.execute('SELECT key, value FROM meta')}
        name = str(meta.get('name') or '').strip()
        version = str(meta.get('version') or '').strip()
        if not name or not version:
            raise DocPackError('Not a docs package: meta has no name or version')
        try:
            count = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
            conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '\"a\"' LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            raise DocPackError(f'Docs package is unreadable: {exc}') from exc
        return {
            'name': name[:200],
            'version': version[:60],
            'description': str(meta.get('description') or '')[:500],
            'source_url': str(meta.get('source_url') or '')[:500],
            'chunk_count': int(count),
        }
    finally:
        conn.close()


# --- the installed set -------------------------------------------------------

def rows(*, enabled_only: bool = False) -> list[dict]:
    sql = 'SELECT * FROM knowledge_docpacks'
    if enabled_only:
        sql += ' WHERE enabled = 1'
    sql += ' ORDER BY name COLLATE NOCASE, created_at DESC'
    return [dict(r) for r in get_db().execute(sql).fetchall()]


def row(pack_id: str) -> dict | None:
    found = get_db().execute(
        'SELECT * FROM knowledge_docpacks WHERE id = ?', (_strip(pack_id),)
    ).fetchone()
    return dict(found) if found else None


def path_for(pack: dict) -> Path:
    return docpacks_root() / pack['filename']


def public(pack: dict) -> dict:
    return {
        'id': public_id(pack['id']),
        'registry': pack['registry'],
        'name': pack['name'],
        'version': pack['version'],
        'description': pack['description'],
        'sourceUrl': pack['source_url'],
        'size': pack['size'],
        'chunkCount': pack['chunk_count'],
        'enabled': bool(pack['enabled']),
        'available': path_for(pack).is_file(),
        'createdAt': pack['created_at'],
    }


def _filename(registry: str, name: str, version: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._@+-]+', '_', f'{registry}__{name}@{version}')
    return f'{safe}.db'


def _adopt(part: Path, registry: str) -> dict:
    """Validate a finished `.part` and move it into place as one row.

    Re-installing a version replaces its file and keeps its row (and so its id
    and enabled flag), rather than accumulating duplicates.
    """
    try:
        meta = validate(part)
    except DocPackError:
        part.unlink(missing_ok=True)
        raise
    filename = _filename(registry, meta['name'], meta['version'])
    final = docpacks_root() / filename
    os.replace(part, final)
    now = int(time.time())
    db = get_db()
    existing = db.execute(
        'SELECT id FROM knowledge_docpacks WHERE registry=? AND name=? AND version=?',
        (registry, meta['name'], meta['version']),
    ).fetchone()
    if existing:
        pack_id = existing['id']
        db.execute(
            'UPDATE knowledge_docpacks SET description=?, source_url=?, filename=?,'
            ' size=?, chunk_count=?, updated_at=? WHERE id=?',
            (meta['description'], meta['source_url'], filename, final.stat().st_size,
             meta['chunk_count'], now, pack_id),
        )
    else:
        pack_id = str(ULID())
        db.execute(
            'INSERT INTO knowledge_docpacks (id, registry, name, version, description,'
            ' source_url, filename, size, chunk_count, enabled, created_at, updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,1,?,?)',
            (pack_id, registry, meta['name'], meta['version'], meta['description'],
             meta['source_url'], filename, final.stat().st_size, meta['chunk_count'],
             now, now),
        )
    db.commit()
    return row(pack_id)


def _part_path() -> Path:
    root = docpacks_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f'.{ULID()}.part'


def import_file(stream) -> dict:
    """Install a package file the user uploaded (a stream with `.read`)."""
    part = _part_path()
    written = 0
    try:
        with part.open('wb') as out:
            while chunk := stream.read(1 << 20):
                written += len(chunk)
                if written > MAX_PACKAGE_BYTES:
                    raise DocPackError('File is too large to be a docs package')
                out.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return _adopt(part, 'local')


def set_enabled(pack_id: str, enabled: bool) -> dict | None:
    db = get_db()
    db.execute('UPDATE knowledge_docpacks SET enabled=?, updated_at=? WHERE id=?',
               (1 if enabled else 0, int(time.time()), _strip(pack_id)))
    db.commit()
    return row(pack_id)


def delete(pack_id: str) -> bool:
    pack = row(pack_id)
    if not pack:
        return False
    path_for(pack).unlink(missing_ok=True)
    db = get_db()
    db.execute('DELETE FROM knowledge_docpacks WHERE id=?', (pack['id'],))
    db.commit()
    return True


# --- the community registry --------------------------------------------------

def _check_spec(registry: str, name: str, version: str | None = None) -> None:
    if not _REGISTRY_RE.match(registry or ''):
        raise DocPackError('Bad registry')
    if not _NAME_RE.match(name or '') or '..' in name:
        raise DocPackError('Bad package name')
    if version is not None and not _VERSION_RE.match(version or ''):
        raise DocPackError('Bad version')


def registry_search(registry: str, name: str) -> list[dict]:
    """Every published version of one package, newest first. The registry
    matches names exactly -- there is no fuzzy search to proxy."""
    registry, name = registry.strip().lower(), name.strip()
    _check_spec(registry, name)
    try:
        resp = requests.get(f'{REGISTRY_URL}/search',
                            params={'registry': registry, 'name': name}, timeout=TIMEOUT)
        resp.raise_for_status()
        found = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RegistryUnavailable(f'Docs registry unavailable: {exc}') from exc
    installed = {(r['registry'], r['name'], r['version']) for r in rows()}
    out = []
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or not item.get('version'):
            continue
        out.append({
            'registry': str(item.get('registry') or registry),
            'name': str(item.get('name') or name),
            'version': str(item['version']),
            'description': str(item.get('description') or ''),
            'size': item.get('size') if isinstance(item.get('size'), int) else None,
            'installed': (registry, str(item.get('name') or name), str(item['version'])) in installed,
        })
    return out


def install(registry: str, name: str, version: str) -> dict:
    """Download one package version from the registry and adopt it.

    Synchronous on purpose: packages are megabytes, not the gigabytes the ZIM
    downloader exists for, so a resumable job table would be machinery with
    nothing to do. A failure leaves neither a row nor a file behind.
    """
    registry, name, version = registry.strip().lower(), name.strip(), version.strip()
    _check_spec(registry, name, version)
    url = (f'{REGISTRY_URL}/packages/{quote(registry, safe="")}/'
           f'{quote(name, safe="")}/{quote(version, safe="")}/download')
    part = _part_path()
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT) as resp:
            if resp.status_code == 404:
                raise DocPackError(f'{registry}/{name}@{version} is not in the registry')
            resp.raise_for_status()
            written = 0
            with part.open('wb') as out:
                for chunk in resp.iter_content(1 << 20):
                    written += len(chunk)
                    if written > MAX_PACKAGE_BYTES:
                        raise DocPackError('Download is too large to be a docs package')
                    out.write(chunk)
    except requests.RequestException as exc:
        part.unlink(missing_ok=True)
        raise RegistryUnavailable(f'Download failed: {exc}') from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return _adopt(part, registry)


# --- search and read ---------------------------------------------------------

def _name_tokens(name: str) -> set[str]:
    return {t for t in _SPLIT.split(name.lower()) if len(t) > 1}


def select(packs: list[dict], tokens: set[str]) -> list[dict]:
    """Which packages to open. All of them while there are few; past that, the
    ones the query names, topped up by the most recently installed."""
    if len(packs) <= MAX_PACKS_PER_SEARCH:
        return packs
    named = [p for p in packs if _name_tokens(p['name']) & tokens]
    chosen = named[:MAX_PACKS_PER_SEARCH]
    picked = {p['id'] for p in chosen}
    for pack in sorted(packs, key=lambda p: -(p['created_at'] or 0)):
        if len(chosen) >= MAX_PACKS_PER_SEARCH:
            break
        if pack['id'] not in picked:
            chosen.append(pack)
            picked.add(pack['id'])
    return chosen


def _hit_title(doc_title: str, section_title: str) -> str:
    doc_title, section_title = doc_title.strip(), section_title.strip()
    if not section_title or section_title == doc_title:
        return doc_title or section_title
    if not doc_title:
        return section_title
    return f'{doc_title} — {section_title}'


def search_pack(pack: dict, query: str, count: int) -> list[tuple[str, str, str]]:
    """`(path, title, snippet)` for one query against one package, best first.

    `path` is the chunk id as text, so a hit fits the `(archiveId, path)` shape
    every ZIM hit already has. Title and section are weighted over the body,
    as neuledge's own search does.
    """
    expr = fts_match_query(query)
    if not expr or count <= 0:
        return []
    conn = _connect(path_for(pack))
    try:
        found = conn.execute(
            'SELECT c.id, c.doc_title, c.section_title,'
            " snippet(chunks_fts, 2, '', '', '…', 24) AS snip"
            ' FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid'
            ' WHERE chunks_fts MATCH ?'
            ' ORDER BY bm25(chunks_fts, 5.0, 10.0, 1.0) LIMIT ?',
            (expr, count),
        ).fetchall()
    finally:
        conn.close()
    return [(str(r['id']), _hit_title(r['doc_title'], r['section_title']), r['snip'] or '')
            for r in found]


def label(pack: dict) -> str:
    return f"{pack['name']} {pack['version']} docs"


def read(identifier: str, chunk_path: str) -> dict:
    """One section with its neighbours from the same document, in order.

    A section alone is often a heading and one paragraph; the model needs the
    surrounding page to use it. The window grows outward from the hit --
    following sections first, since docs explain forwards -- until the same
    budget a ZIM article read gets.
    """
    pack = row(identifier)
    if not pack:
        raise LookupError(identifier)
    try:
        chunk_id = int(str(chunk_path).strip('/'))
    except ValueError as exc:
        raise LookupError(chunk_path) from exc
    conn = _connect(path_for(pack))
    try:
        target = conn.execute(
            'SELECT id, doc_path, doc_title, section_title, content FROM chunks WHERE id=?',
            (chunk_id,),
        ).fetchone()
        if target is None:
            raise LookupError(chunk_path)
        siblings = conn.execute(
            'SELECT id, section_title, content FROM chunks WHERE doc_path=? ORDER BY id',
            (target['doc_path'],),
        ).fetchall()
    finally:
        conn.close()

    index = next(i for i, s in enumerate(siblings) if s['id'] == chunk_id)
    lo = hi = index
    used = len(siblings[index]['content'])
    grow_after = grow_before = True
    while grow_after or grow_before:
        if grow_after:
            if hi + 1 < len(siblings) and used + len(siblings[hi + 1]['content']) <= MAX_ARTICLE_CHARS:
                hi += 1
                used += len(siblings[hi]['content'])
            else:
                grow_after = False
        if grow_before:
            if lo > 0 and used + len(siblings[lo - 1]['content']) <= MAX_ARTICLE_CHARS:
                lo -= 1
                used += len(siblings[lo]['content'])
            else:
                grow_before = False
    text = '\n\n'.join(s['content'].strip() for s in siblings[lo:hi + 1])
    return {
        'archiveId': public_id(pack['id']),
        'archiveTitle': label(pack),
        'archiveDate': '',
        'path': str(chunk_id),
        'docPath': target['doc_path'],
        'title': _hit_title(target['doc_title'], target['section_title']),
        'text': text[:MAX_ARTICLE_CHARS],
    }
