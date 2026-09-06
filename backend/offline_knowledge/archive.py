"""Discovery, search and article reads over Kiwix ZIM files.

The archive drive remains the source of truth: Lunaschal stores only its root
path in settings and never writes beside, copies, or modifies a ZIM file.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.db.connection import get_db
from backend.htmltext import strip_html_with_title

MAX_ARCHIVES = 500
MAX_RESULTS = 25
MAX_ARTICLE_CHARS = 12_000
MAX_MODEL_QUERIES = 4
MAX_MODEL_RESULTS = 16
RESULTS_PER_QUERY = 8
_lock = threading.RLock()  # libzim search objects are not thread-safe.


class KnowledgeUnavailable(RuntimeError):
    pass


class ArchiveNotFound(LookupError):
    pass


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


@lru_cache(maxsize=32)
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


def _summary(path: Path, zim: Any) -> dict:
    return {
        'id': archive_id(path),
        'filename': path.name,
        'title': _metadata(zim, 'Title', path.stem),
        'description': _metadata(zim, 'Description'),
        'language': _metadata(zim, 'Language'),
        'date': _metadata(zim, 'Date'),
        'articleCount': _call_value(zim, 'article_count', 'getArticleCount'),
        'size': path.stat().st_size,
        'hasFulltextIndex': bool(
            _call_value(zim, 'has_fulltext_index', 'hasFulltextIndex', default=True)
        ),
    }


def list_archives() -> list[dict]:
    out = []
    with _lock:
        for path in _paths():
            try:
                out.append(_summary(path, _archive(path)))
            except Exception as exc:
                out.append({
                    'id': archive_id(path), 'filename': path.name,
                    'title': path.stem, 'size': path.stat().st_size,
                    'error': str(exc),
                })
    return out


def _resolve(identifier: str) -> Path:
    for path in _paths():
        if archive_id(path) == identifier:
            return path
    raise ArchiveNotFound(identifier)


def search(query_text: str, *, limit: int = 10) -> list[dict]:
    query_text = query_text.strip()
    if not query_text:
        return []
    limit = max(1, min(MAX_RESULTS, int(limit)))
    _, Query, Searcher = _libzim()
    results: list[dict] = []
    with _lock:
        for path in _paths():
            if len(results) >= limit:
                break
            try:
                zim = _archive(path)
                remaining = limit - len(results)
                found = Searcher(zim).search(Query().set_query(query_text))
                matches = int(_call_value(found, 'getEstimatedMatches', default=0) or 0)
                for hit in found.getResults(0, min(remaining, matches)):
                    # python-libzim's SearchResultSet yields entry paths. Keep
                    # the object fallback for bindings that expose the richer
                    # C++ iterator instead.
                    article_path = (
                        hit if isinstance(hit, str)
                        else str(_call_value(hit, 'path', 'getPath', default=''))
                    )
                    if not article_path:
                        continue
                    try:
                        entry = zim.get_entry_by_path(article_path.lstrip('/'))
                        title = str(_call_value(entry, 'title', 'getTitle', default=article_path))
                    except Exception:
                        title = article_path
                    results.append({
                        'archiveId': archive_id(path),
                        'archiveTitle': _metadata(zim, 'Title', path.stem),
                        'archiveDate': _metadata(zim, 'Date'),
                        'path': article_path.lstrip('/'),
                        'title': title,
                        'snippet': str(_call_value(hit, 'snippet', 'getSnippet', default='')),
                    })
            except Exception:
                continue
    return results


def read_entry(identifier: str, entry_path: str) -> tuple[bytes, str, dict]:
    path = _resolve(identifier)
    clean = entry_path.lstrip('/')
    with _lock:
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
    """Search several variants and return one ranked, de-duplicated list."""
    queries = _model_queries(query_values)
    if not queries:
        return 'Local knowledge search needs at least one query.', {
            'tool': 'local_knowledge_search', 'arg': '', 'queries': [],
            'ok': False, 'count': 0, 'error': 'no search queries provided',
        }
    candidates: dict[tuple[str, str], dict] = {}
    first_seen = 0
    try:
        for query in queries:
            for rank, hit in enumerate(search(query, limit=RESULTS_PER_QUERY)):
                key = (hit['archiveId'], hit['path'])
                candidate = candidates.get(key)
                if candidate is None:
                    candidate = {
                        **hit,
                        '_firstSeen': first_seen,
                        '_bestRank': rank,
                        '_matches': [],
                    }
                    candidates[key] = candidate
                    first_seen += 1
                candidate['_bestRank'] = min(candidate['_bestRank'], rank)
                candidate['_matches'].append(query)
    except Exception as exc:
        return f'Local knowledge search unavailable: {exc}', {
            'tool': 'local_knowledge_search', 'arg': queries[0], 'queries': queries,
            'ok': False, 'count': 0, 'error': str(exc),
        }

    def rank_key(hit: dict) -> tuple:
        title_scores = [_title_match(hit['title'], query) for query in queries]
        tier, coverage = max(title_scores)
        return (-tier, -coverage, hit['_bestRank'], hit['_firstSeen'])

    bounded_limit = max(1, min(MAX_MODEL_RESULTS, int(limit)))
    hits = sorted(candidates.values(), key=rank_key)[:bounded_limit]
    lines = []
    for hit in hits:
        snippet = html.unescape(hit['snippet']).replace('\n', ' ').strip()
        matched = '; '.join(hit['_matches'])
        lines.append(
            f"- {hit['title']} [{hit['archiveTitle']} {hit['archiveDate']}]\n"
            f"  archiveId={hit['archiveId']} path={hit['path']}\n"
            f"  matched queries: {matched}\n  {snippet}"
        )
    text = '\n'.join(lines) if lines else 'No matching local articles were found.'
    text += ('\n\nThis is a merged candidate list. Choose by title, source, and the user\'s '
             'intended meaning. Read the best one to three articles before using them as '
             'evidence; if the title is ambiguous, inspect each plausible interpretation. '
             'Use web research only if local coverage remains insufficient or stale.')
    return text, {
        'tool': 'local_knowledge_search', 'arg': queries[0], 'queries': queries,
        'ok': True, 'count': len(hits),
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
