"""Discovery, search and article reads over Kiwix ZIM files.

The archive drive remains the source of truth: Lunaschal stores only its root
path in settings and never writes beside, copies, or modifies a ZIM file.

**Search is federated, and that is the whole design.** The first version walked
the archives in filename order and stopped as soon as the global result limit
was full, which was invisible with one Wikipedia installed and fatal with two
archives of different sizes: `stackoverflow.com_en_all` (107 GB, 30.6 M
articles) sorts before `wikipedia_`, so it would spend every slot before an
encyclopedia was ever opened. `search_many` instead gives each *kind* of source
a share of the answer, searches a bounded set of archives inside each, and
merges globally -- see the three-stage comment on `search_many` itself.

Two things measured on the real library shaped this, and neither is obvious:

* **Parallelism does not pay.** Sixteen warm searches across four distinct
  `Archive` objects took 0.097 s serially and 0.109 s through a four-thread
  pool -- the bindings hold the GIL for the duration of a search. So the lever
  is searching *fewer* archives, not searching them at once. The per-path locks
  below are about not blocking an unrelated article read, not about throughput.
* **Half the interesting archives have no fulltext index.** Every DevDocs ZIM
  Kiwix publishes is `_ftindex:no`, as are 7 of the 181 Stack Exchange ones.
  `Searcher` returns nothing at all for those; they answer through
  `SuggestionSearcher`, which queries the title index. An archive without a
  fulltext index is a normal archive here, not a broken one.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.db.connection import get_db
from backend.htmltext import strip_html_with_title

# A guard on the rglob, not on the hot path: discovery now happens inside
# registry.sync() behind a TTL, and search reads the table. The DevDocs
# collection alone is several hundred files.
MAX_ARCHIVES = 2000
MAX_RESULTS = 25
MAX_ARTICLE_CHARS = 12_000
MAX_MODEL_QUERIES = 4
MAX_MODEL_RESULTS = 16
RESULTS_PER_QUERY = 8

# How much of one answer each kind of source may occupy. Unused share is
# redistributed in the final pass, so a library with no Q&A archives does not
# waste 30% of every result list.
CLASS_SHARE = {'encyclopedia': 0.40, 'qa': 0.30, 'docs': 0.20, 'other': 0.10}
# Tiebreak only, after title match and normalized rank.
CLASS_PRIOR = {'encyclopedia': 0, 'qa': 1, 'docs': 1, 'other': 2}
# The most archives of one kind to open for a single search. Reached only by
# the large collections (DevDocs, Stack Exchange); a class smaller than this is
# searched in full.
MAX_ARCHIVES_PER_CLASS = 8
# Wall clock for the whole federated pass. A search that has already spent this
# returns what it has and reports the rest as skipped, rather than making a
# chat reply wait on the tail of a large library.
SEARCH_BUDGET_MS = 4000


class KnowledgeUnavailable(RuntimeError):
    pass


class ArchiveNotFound(LookupError):
    pass


def _registry():
    """Imported lazily -- registry.py reads this module's libzim primitives, so
    a module-level import in this direction would be a cycle."""
    from backend.offline_knowledge import registry
    return registry


def configured_root() -> Path | None:
    row = get_db().execute('SELECT knowledge_root FROM settings LIMIT 1').fetchone()
    value = row['knowledge_root'] if row else None
    value = value or os.environ.get('KNOWLEDGE_ROOT')
    if not value:
        return None
    try:
        return Path(value).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def archive_id(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:20]


def _paths() -> list[Path]:
    root = configured_root()
    if not root or not root.is_dir():
        return []
    found: list[Path] = []
    try:
        for path in root.rglob('*.zim'):
            if path.is_file():
                found.append(path.resolve())
                if len(found) >= MAX_ARCHIVES:
                    break
    except (OSError, PermissionError):
        return []
    return sorted(found, key=lambda p: p.name.lower())


def _libzim():
    try:
        from libzim.reader import Archive
        from libzim.search import Query, Searcher
    except (ImportError, OSError) as exc:
        raise KnowledgeUnavailable(
            'ZIM support is not installed; install the libzim Python package.'
        ) from exc
    return Archive, Query, Searcher


def _suggestion():
    """The title-index searcher, for archives built without a fulltext index.

    Separate from `_libzim` so that a libzim too old to ship
    `libzim.suggestion` degrades to "no-fulltext archives are unsearchable"
    rather than taking the whole library down with an ImportError.
    """
    try:
        from libzim.suggestion import SuggestionSearcher
    except (ImportError, OSError):
        return None
    return SuggestionSearcher


# libzim's search objects are not thread-safe, but that is a per-archive
# property. One global lock made a chat search serialise against an iframe
# reading a different archive entirely, which is the contention that actually
# happens here.
_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(path: Path | str) -> threading.RLock:
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


@lru_cache(maxsize=64)
def _open(path_text: str, mtime_ns: int):
    Archive, _, _ = _libzim()
    return Archive(path_text)


def _archive(path: Path):
    try:
        stamp = path.stat().st_mtime_ns
    except OSError as exc:
        raise ArchiveNotFound(str(path)) from exc
    return _open(str(path), stamp)


def _call_value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if not hasattr(obj, name):
            continue
        value = getattr(obj, name)
        try:
            return value() if callable(value) else value
        except Exception:
            continue
    return default


def _metadata(zim: Any, name: str, default: str = '') -> str:
    try:
        value = zim.get_metadata(name)
        if isinstance(value, bytes):
            return value.decode('utf-8', 'replace')
        return str(value or default)
    except Exception:
        return default


def _public(row: dict) -> dict:
    """One registry row in the shape the reader and the API speak."""
    return {
        'id': row['id'],
        'filename': row['filename'],
        'title': row['title'] or Path(row['filename']).stem,
        'language': row['language'],
        'date': row['zim_date'],
        'flavour': row['flavour'],
        'size': row['size'],
        'articleCount': row['article_count'],
        'kind': row['kind'],
        'kindSource': row['kind_source'],
        'enabled': bool(row['enabled']),
        'hasFulltextIndex': bool(row['has_fulltext_index']),
        'hasTitleIndex': bool(row['has_title_index']),
        'health': row['health'],
        'error': row['health_error'],
    }


def list_archives() -> list[dict]:
    """Every known archive, disabled and unhealthy ones included.

    The reader needs to show a disabled or missing archive -- that is how the
    user turns it back on or notices the drive is unplugged -- so this is the
    one caller that asks for everything.
    """
    registry = _registry()
    registry.ensure_synced()
    return [_public(row) for row in
            registry.rows(enabled_only=False, healthy_only=False)]


def _resolve(identifier: str) -> Path:
    """The file behind an archive id: one indexed SELECT.

    This used to rglob the entire root on every article read, which with a few
    hundred archives is a directory walk per image in a rendered page.
    """
    path = _registry().path_for(identifier)
    if path is None:
        # A file dropped in since the last scan is still readable: sync once
        # before giving up, rather than 404ing something that is on disk.
        _registry().sync()
        path = _registry().path_for(identifier)
    if path is None:
        raise ArchiveNotFound(identifier)
    return path


def _extract(found: Any, zim: Any, count: int) -> list[tuple[str, str, str]]:
    """`(path, title, snippet)` for up to `count` hits of a result set.

    python-libzim's result sets yield entry *paths* as plain strings; the
    object fallback is kept for bindings that expose the richer C++ iterator.
    Both `Searcher` and `SuggestionSearcher` results have this shape, which is
    why the two search paths share one extractor.
    """
    out: list[tuple[str, str, str]] = []
    estimated = int(_call_value(found, 'getEstimatedMatches', default=0) or 0)
    if estimated <= 0 or count <= 0:
        return out
    for hit in found.getResults(0, min(count, estimated)):
        article_path = (
            hit if isinstance(hit, str)
            else str(_call_value(hit, 'path', 'getPath', default=''))
        )
        if not article_path:
            continue
        clean = article_path.lstrip('/')
        try:
            entry = zim.get_entry_by_path(clean)
            title = str(_call_value(entry, 'title', 'getTitle', default=clean))
        except Exception:
            title = clean
        snippet = str(_call_value(hit, 'snippet', 'getSnippet', default=''))
        out.append((clean, title, snippet))
    return out


def _query_archive(zim: Any, row: dict, query_text: str,
                   count: int) -> list[tuple[str, str, str]]:
    """Run one query against one open archive, by whichever index it has."""
    if row.get('has_fulltext_index', 1):
        _, Query, Searcher = _libzim()
        found = Searcher(zim).search(Query().set_query(query_text))
        return _extract(found, zim, count)

    SuggestionSearcher = _suggestion()
    if SuggestionSearcher is None or not row.get('has_title_index', 1):
        return []
    found = SuggestionSearcher(zim).suggest(query_text)
    return _extract(found, zim, count)


def _select(rows: list[dict], tokens: set[str],
            kinds_wanted=None) -> dict[str, list[dict]]:
    """Which archives to open, grouped by kind.

    A class small enough to search in full is searched in full. A large one --
    DevDocs at several hundred, Stack Exchange at 181 -- is narrowed to the
    archives the query actually names (`match_terms`), topped up by article
    count so that a programming question with no term match still reaches Stack
    Overflow rather than reaching nothing.
    """
    from backend.offline_knowledge import kinds as kinds_mod

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if kinds_wanted and row['kind'] not in kinds_wanted:
            continue
        grouped.setdefault(row['kind'], []).append(row)

    out: dict[str, list[dict]] = {}
    for kind, items in grouped.items():
        if len(items) <= MAX_ARCHIVES_PER_CLASS:
            out[kind] = items
            continue
        chosen = [r for r in items
                  if kinds_mod.matches_query(r['match_terms'], tokens)]
        chosen = chosen[:MAX_ARCHIVES_PER_CLASS]
        if len(chosen) < MAX_ARCHIVES_PER_CLASS:
            picked = {r['id'] for r in chosen}
            biggest = sorted(items, key=lambda r: -(r['article_count'] or 0))
            for row in biggest:
                if len(chosen) >= MAX_ARCHIVES_PER_CLASS:
                    break
                if row['id'] not in picked:
                    chosen.append(row)
                    picked.add(row['id'])
        out[kind] = chosen
    return {k: v for k, v in out.items() if v}


def _class_quotas(selected: dict[str, list[dict]], limit: int) -> dict[str, int]:
    shares = {kind: CLASS_SHARE.get(kind, CLASS_SHARE['other']) for kind in selected}
    total = sum(shares.values()) or 1.0
    return {kind: max(1, int(round(limit * share / total)))
            for kind, share in shares.items()}


def search_many(queries, *, per_query: int = RESULTS_PER_QUERY,
                limit: int = MAX_RESULTS, kinds_wanted=None,
                archive_ids=None, budget_ms: int = SEARCH_BUDGET_MS) -> dict:
    """Search every relevant archive and return one fairly merged list.

    Three stages, and the middle one is the reason this exists:

    1. **Select.** Registry rows only -- no archive is opened to decide whether
       to open it. Each kind contributes at most `MAX_ARCHIVES_PER_CLASS`.
    2. **Search, archive-outer and query-inner.** Each archive is opened once
       and asked all of the queries, so a four-variant model search costs N
       archive visits rather than 4N. Each archive gets its own quota, so a
       huge one cannot fill the list.
    3. **Merge globally.** Deduplicate on `(archiveId, path)`, rank by title
       match first -- the cross-archive equaliser, since rank position means
       nothing between a 739 KB and a 107 GB index -- then by rank normalized
       against the archive's own quota, then by class prior.
    """
    from backend.offline_knowledge import kinds as kinds_mod

    queries = [q for q in (queries or []) if str(q).strip()]
    # Registry history survives removing the folder setting, but must not
    # keep that library active. configured_root also honors KNOWLEDGE_ROOT.
    if not queries or configured_root() is None:
        return {'results': [], 'searched': [], 'skipped': 0, 'tookMs': 0}
    limit = max(1, min(MAX_RESULTS, int(limit)))
    started = time.monotonic()
    deadline = started + max(0.1, budget_ms / 1000.0)

    registry = _registry()
    registry.ensure_synced()
    rows = registry.rows(enabled_only=True, healthy_only=True)
    if archive_ids:
        wanted = set(archive_ids)
        rows = [r for r in rows if r['id'] in wanted]

    tokens: set[str] = set()
    for query in queries:
        tokens |= kinds_mod.query_tokens(query)
    selected = _select(rows, tokens, kinds_wanted)
    quotas = _class_quotas(selected, limit)

    candidates: dict[tuple[str, str], dict] = {}
    searched: list[str] = []
    skipped = 0
    first_seen = 0

    for kind, items in selected.items():
        # How much is *fetched* is deliberately not the class quota. Fetching
        # only as many as may be returned leaves nothing to redistribute when
        # another class under-delivers, so a two-archive library answered 7 of
        # a requested 10 while a 50-hit archive sat right there. The quota
        # governs the output; the fetch is a flat per-archive depth, which also
        # makes rank position comparable between a tiny index and a huge one.
        # One archive may need to fill the entire output when it is the only
        # source (or the other sources have no hits).
        per_archive = max(per_query, limit)
        for row in items:
            if time.monotonic() > deadline:
                skipped += 1
                continue
            path = Path(row['path'])
            try:
                with _lock_for(path):
                    zim = _archive(path)
                    for query in queries:
                        hits = _query_archive(zim, row, query, per_archive)
                        for rank, (article_path, title, snippet) in enumerate(hits):
                            key = (row['id'], article_path)
                            candidate = candidates.get(key)
                            if candidate is None:
                                candidate = {
                                    'archiveId': row['id'],
                                    'archiveTitle': row['title'],
                                    'archiveDate': row['zim_date'],
                                    'archiveKind': kind,
                                    'path': article_path,
                                    'title': title,
                                    'snippet': snippet,
                                    'matchKind': ('fulltext'
                                                  if row.get('has_fulltext_index', 1)
                                                  else 'title'),
                                    '_rank': rank,
                                    '_norm': rank / max(1, per_archive),
                                    '_firstSeen': first_seen,
                                    '_matches': [],
                                }
                                candidates[key] = candidate
                                first_seen += 1
                            if rank < candidate['_rank']:
                                candidate['_rank'] = rank
                                candidate['_norm'] = rank / max(1, per_archive)
                            if not candidate['snippet'] and snippet:
                                candidate['snippet'] = snippet
                            candidate['_matches'].append(query)
            except Exception:
                # One unreadable archive must not fail a search the rest of the
                # library could answer. The scan records health separately.
                skipped += 1
                continue
            searched.append(row['id'])

    def rank_key(hit: dict) -> tuple:
        tier, coverage = max(_title_match(hit['title'], q) for q in queries)
        return (-tier, -coverage, hit['_norm'],
                CLASS_PRIOR.get(hit['archiveKind'], 2), hit['_firstSeen'])

    ordered = sorted(candidates.values(), key=rank_key)

    # Fill each class's share first, then top up from whatever is left -- which
    # is what redistributes the share of a class the library does not have.
    taken: list[dict] = []
    used = {kind: 0 for kind in selected}
    for hit in ordered:
        kind = hit['archiveKind']
        if len(taken) >= limit:
            break
        if used.get(kind, 0) < quotas.get(kind, limit):
            taken.append(hit)
            used[kind] = used.get(kind, 0) + 1
    if len(taken) < limit:
        chosen = {(h['archiveId'], h['path']) for h in taken}
        for hit in ordered:
            if len(taken) >= limit:
                break
            if (hit['archiveId'], hit['path']) not in chosen:
                taken.append(hit)
    taken.sort(key=rank_key)

    return {
        'results': taken,
        'searched': searched,
        'skipped': skipped,
        'tookMs': int((time.monotonic() - started) * 1000),
    }


def search(query_text: str, *, limit: int = 10) -> list[dict]:
    """One query, flat result list -- the shape the reader and older callers use."""
    query_text = (query_text or '').strip()
    if not query_text:
        return []
    found = search_many([query_text], limit=min(MAX_RESULTS, max(1, int(limit))))
    return [{k: v for k, v in hit.items() if not k.startswith('_')}
            for hit in found['results']]


def read_entry(identifier: str, entry_path: str) -> tuple[bytes, str, dict]:
    path = _resolve(identifier)
    clean = entry_path.lstrip('/')
    with _lock_for(path):
        zim = _archive(path)
        try:
            entry = zim.get_entry_by_path(clean)
        except Exception as exc:
            raise ArchiveNotFound(clean) from exc
        item = entry.get_item()
        content = bytes(item.content)
        mime = str(_call_value(item, 'mimetype', 'getMimetype', default='application/octet-stream'))
        meta = {
            'archiveId': identifier,
            'archiveTitle': _metadata(zim, 'Title', path.stem),
            'archiveDate': _metadata(zim, 'Date'),
            'path': clean,
            'title': str(_call_value(entry, 'title', 'getTitle', default=clean)),
        }
        return content, mime, meta


def read_article(identifier: str, entry_path: str) -> dict:
    content, mime, meta = read_entry(identifier, entry_path)
    if mime not in ('text/html', 'application/xhtml+xml', 'text/plain'):
        raise ValueError(f'Entry is not readable text ({mime})')
    decoded = content.decode('utf-8', 'replace')
    if mime == 'text/plain':
        text = decoded
    else:
        text, _ = strip_html_with_title(decoded)
    text = text.strip()[:MAX_ARTICLE_CHARS]
    return {**meta, 'text': text}


def _model_queries(values: str | list[str]) -> list[str]:
    """Bound and de-duplicate model-authored query variants."""
    raw = [values] if isinstance(values, str) else values
    queries: list[str] = []
    seen: set[str] = set()
    for value in raw or []:
        query = str(value or '').strip()[:300]
        key = query.casefold()
        if not query or key in seen:
            continue
        queries.append(query)
        seen.add(key)
        if len(queries) >= MAX_MODEL_QUERIES:
            break
    return queries


def _title_key(value: str) -> str:
    return ' '.join(re.findall(r'\w+', value.casefold()))


def _title_match(title: str, query: str) -> tuple[int, float]:
    """Prefer exact titles, then parenthetical variants, then token coverage.

    Kiwix full-text ranking is still useful for candidate generation, but a
    question-shaped query can put a page that merely contains all its words
    above the page actually named by the user. This inexpensive pass only
    orders the merged candidate list; the chat model still decides what to
    read and cannot use a title as evidence.

    It carries more weight now than it did: across archives it is the only
    comparable signal there is, because rank position inside a 739 KB DevDocs
    index and inside a 30-million-article Stack Overflow index mean nothing to
    each other.
    """
    title_key = _title_key(title)
    query_key = _title_key(query)
    if not title_key or not query_key:
        return 0, 0.0
    if title_key == query_key:
        return 4, 1.0
    title_base = _title_key(re.sub(r'\s*\([^)]*\)\s*$', '', title))
    if title_base == query_key:
        return 3, 1.0
    if query_key in title_key or title_key in query_key:
        coverage = min(len(title_key), len(query_key)) / max(
            len(title_key), len(query_key)
        )
        return 2, coverage
    title_tokens = set(title_key.split())
    query_tokens = set(query_key.split())
    return 1, len(title_tokens & query_tokens) / max(1, len(query_tokens))


def model_search(
    query_values: str | list[str], *, limit: int = MAX_MODEL_RESULTS
) -> tuple[str, dict]:
    """Search several variants and return one ranked, de-duplicated list.

    The merge itself now lives in `search_many`, which can see which archive
    each hit came from and therefore rank fairly across them; what stays here
    is the model-facing bounding of the queries and the rendering.
    """
    queries = _model_queries(query_values)
    if not queries:
        return 'Local knowledge search needs at least one query.', {
            'tool': 'local_knowledge_search', 'arg': '', 'queries': [],
            'ok': False, 'count': 0, 'error': 'no search queries provided',
        }
    bounded_limit = max(1, min(MAX_MODEL_RESULTS, int(limit)))
    try:
        found = search_many(queries, per_query=RESULTS_PER_QUERY, limit=bounded_limit)
    except Exception as exc:
        return f'Local knowledge search unavailable: {exc}', {
            'tool': 'local_knowledge_search', 'arg': queries[0], 'queries': queries,
            'ok': False, 'count': 0, 'error': str(exc),
        }

    lines = []
    for hit in found['results']:
        snippet = html.unescape(hit['snippet']).replace('\n', ' ').strip()
        matched = '; '.join(dict.fromkeys(hit['_matches']))
        lines.append(
            f"- {hit['title']} [{hit['archiveTitle']} {hit['archiveDate']}"
            f" · {hit['archiveKind']}]\n"
            f"  archiveId={hit['archiveId']} path={hit['path']}\n"
            f"  matched queries: {matched}\n  {snippet}"
        )
    text = '\n'.join(lines) if lines else 'No matching local articles were found.'
    text += ('\n\nThis is a merged candidate list drawn from every relevant archive. '
             'Choose by title, source kind, and the user\'s intended meaning — an '
             'encyclopedia article, a Q&A thread and an API reference page answer '
             'different shapes of question. Read the best one to three articles '
             'before using them as evidence; if the title is ambiguous, inspect each '
             'plausible interpretation. Use web research only if local coverage '
             'remains insufficient or stale.')
    return text, {
        'tool': 'local_knowledge_search', 'arg': queries[0], 'queries': queries,
        'ok': True, 'count': len(found['results']),
        'searched': len(found['searched']), 'skipped': found['skipped'],
    }


def model_read(identifier: str, entry_path: str) -> tuple[str, dict]:
    try:
        article = read_article(identifier, entry_path)
    except Exception as exc:
        return f'Could not read local article: {exc}', {
            'tool': 'local_knowledge_read', 'arg': entry_path,
            'ok': False, 'error': str(exc),
        }
    url = f"/api/knowledge/archives/{identifier}/content/{entry_path.lstrip('/')}"
    header = f"# {article['title']}\nSource: {article['archiveTitle']} ({article['archiveDate'] or 'date unknown'})\n"
    return f"{header}\n{article['text']}", {
        'tool': 'local_knowledge_read', 'arg': article['title'], 'title': article['title'],
        'ok': True, 'url': url,
        # The shared loop collects nested/multi-source tools through this
        # generic field; local reads are citations just as web_fetch reads are.
        'sources': [{'url': url, 'title': article['title']}],
        # A bounded, durable evidence record is kept with the assistant reply.
        # The full 12K tool result is useful to this answer turn but should not
        # be replayed on every later turn; the stable ids let the agent reopen
        # the article when this excerpt is not enough.
        'evidence': {
            'kind': 'offline_knowledge',
            'archiveId': identifier,
            'archiveTitle': article.get('archiveTitle', ''),
            'archiveDate': article.get('archiveDate', ''),
            'path': article.get('path', entry_path.lstrip('/')),
            'title': article['title'],
            'url': url,
            'excerpt': article['text'][:2000],
        },
    }
