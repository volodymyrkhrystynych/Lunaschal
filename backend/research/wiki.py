"""The LLM wiki: agent-written articles, with an audit trail.

Two decisions are load-bearing:

**Writes are copy-on-write.** Before any edit the *previous* title and content
go into wiki_revisions with a unified diff, then `revision` increments. The
agent is a background process editing prose the user relies on; being able to
see and undo what it did is what makes that acceptable. A `locked` article is
the user's — the agent is told to propose changes in its answer instead.

**Retrieval hands the model the whole index, not just a retriever.** At a few
dozen articles, `wiki_list()` returning {slug, title, summary} for everything
costs ~1,200 tokens and lets the model pick by name, which beats any ranking
function and costs one query. FTS is the fallback above WIKI_INDEX_MAX, and the
escape hatch for re-querying with different words after a miss. Embeddings are
deliberately not used: the `embed` alias has ctx-size 2048 so an article would
need chunking, and its vectors are frozen because learning_cards depends on
them. Revisit above ~300 articles.
"""
import difflib
import json
import re
import time

from ulid import ULID

from backend.db.connection import get_db, row_to_dict, search_wiki_fts
from backend.tags import tags_json

WIKI_INDEX_MAX = 60
MAX_ARTICLE_CHARS = 8000
MAX_SUMMARY_CHARS = 400


class ArticleLocked(PermissionError):
    """The user owns this article; the agent may not overwrite it."""


def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (text or '').strip().lower()).strip('-')
    return slug[:80] or 'untitled'


def get_article(slug: str) -> dict | None:
    row = get_db().execute('SELECT * FROM wiki_articles WHERE slug=?', (slug,)).fetchone()
    return row_to_dict(row) if row else None


def list_articles(limit: int = WIKI_INDEX_MAX) -> list[dict]:
    """The index: enough to choose an article, cheap enough to inline."""
    rows = get_db().execute(
        'SELECT id, slug, title, summary, revision, locked, updated_at'
        ' FROM wiki_articles ORDER BY updated_at DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def search_articles(query: str, limit: int = 5) -> list[dict]:
    """FTS-ranked articles, in rank order."""
    hits = search_wiki_fts(query, limit)
    if not hits:
        return []
    db = get_db()
    by_id = {}
    placeholders = ','.join('?' * len(hits))
    for row in db.execute(
        f'SELECT id, slug, title, summary FROM wiki_articles WHERE id IN ({placeholders})',
        [h['id'] for h in hits],
    ).fetchall():
        by_id[row['id']] = row_to_dict(row)
    return [by_id[h['id']] for h in hits if h['id'] in by_id]


def upsert_article(
    slug: str,
    title: str,
    summary: str,
    content: str,
    *,
    sources: list[dict] | None = None,
    tags: list[str] | None = None,
    author: str = 'agent',
    note: str | None = None,
    now: int | None = None,
) -> dict:
    """Create or revise an article, logging the previous version first."""
    slug = slugify(slug or title)
    now = now or int(time.time())
    summary = (summary or '').strip()[:MAX_SUMMARY_CHARS]
    content = (content or '').strip()[:MAX_ARTICLE_CHARS]
    db = get_db()

    existing = db.execute('SELECT * FROM wiki_articles WHERE slug=?', (slug,)).fetchone()
    sources_json = json.dumps(sources) if sources else None

    if existing is None:
        article_id = str(ULID())
        db.execute(
            'INSERT INTO wiki_articles(id, slug, title, summary, content, sources,'
            ' tags, revision, locked, last_researched_at, created_at, updated_at)'
            ' VALUES (?,?,?,?,?,?,?,1,0,?,?,?)',
            (article_id, slug, title.strip(), summary, content, sources_json,
             tags_json(tags), now, now, now),
        )
        db.execute(
            'INSERT INTO wiki_revisions(id, article_id, revision, title, content,'
            ' diff, author, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (str(ULID()), article_id, 1, title.strip(), content, None, author,
             note or 'created', now),
        )
        db.commit()
        return get_article(slug)

    if existing['locked'] and author == 'agent':
        raise ArticleLocked(f'{slug} is locked by the user')

    revision = existing['revision'] + 1
    diff = '\n'.join(difflib.unified_diff(
        (existing['content'] or '').splitlines(),
        content.splitlines(),
        fromfile=f'{slug}@{existing["revision"]}',
        tofile=f'{slug}@{revision}',
        lineterm='',
    ))
    # The revision row holds the version being *replaced*, so the history reads
    # as "what it used to say" rather than a duplicate of the current row.
    db.execute(
        'INSERT INTO wiki_revisions(id, article_id, revision, title, content,'
        ' diff, author, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (str(ULID()), existing['id'], existing['revision'], existing['title'],
         existing['content'], diff, author, note, now),
    )
    db.execute(
        'UPDATE wiki_articles SET title=?, summary=?, content=?, sources=?,'
        ' tags=COALESCE(?, tags), revision=?, last_researched_at=?, updated_at=?'
        ' WHERE id=?',
        (title.strip(), summary, content, sources_json, tags_json(tags),
         revision, now, now, existing['id']),
    )
    db.commit()
    return get_article(slug)


def revisions(article_id: str) -> list[dict]:
    rows = get_db().execute(
        'SELECT * FROM wiki_revisions WHERE article_id=? ORDER BY revision DESC',
        (article_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def link_idea(idea_id: str, article_id: str, relevance: float = 1.0) -> None:
    db = get_db()
    db.execute(
        'INSERT OR REPLACE INTO idea_wiki_links(idea_id, article_id, relevance, created_at)'
        ' VALUES (?,?,?,?)',
        (idea_id, article_id, relevance, int(time.time())),
    )
    db.commit()


def articles_for_idea(idea_id: str) -> list[dict]:
    rows = get_db().execute(
        'SELECT a.id, a.slug, a.title, a.summary, l.relevance FROM idea_wiki_links l'
        ' JOIN wiki_articles a ON a.id = l.article_id'
        ' WHERE l.idea_id=? ORDER BY l.relevance DESC, a.updated_at DESC',
        (idea_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


# --- Tools ---

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'wiki_list',
            'description': (
                'List every article in the research wiki with its one-line '
                'summary. Use this first to see what is already known.'
            ),
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'wiki_search',
            'description': 'Search the research wiki by keyword.',
            'parameters': {
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'wiki_read',
            'description': 'Read a wiki article in full by its slug.',
            'parameters': {
                'type': 'object',
                'properties': {'slug': {'type': 'string'}},
                'required': ['slug'],
            },
        },
    },
]


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Execute a wiki tool call. Never raises, for the same reason as web.run_tool."""
    if name == 'wiki_list':
        articles = list_articles()
        if not articles:
            return ('The wiki is empty.', {'tool': 'wiki_list', 'ok': True, 'count': 0})
        lines = [f"- {a['slug']}: {a['title']} — {a['summary']}" for a in articles]
        return ('\n'.join(lines), {'tool': 'wiki_list', 'ok': True, 'count': len(articles)})

    if name == 'wiki_search':
        query = (args.get('query') or '').strip()
        hits = search_articles(query)
        if not hits:
            return (
                f'No wiki articles match "{query}".',
                {'tool': 'wiki_search', 'arg': query, 'ok': True, 'count': 0},
            )
        lines = [f"- {a['slug']}: {a['title']} — {a['summary']}" for a in hits]
        return (
            '\n'.join(lines),
            {'tool': 'wiki_search', 'arg': query, 'ok': True, 'count': len(hits)},
        )

    if name == 'wiki_read':
        slug = (args.get('slug') or '').strip()
        article = get_article(slug)
        if not article:
            return (
                f'No wiki article with slug "{slug}".',
                {'tool': 'wiki_read', 'arg': slug, 'ok': False},
            )
        return (
            f"# {article['title']}\n\n{article['content']}",
            {'tool': 'wiki_read', 'arg': slug, 'ok': True, 'title': article['title']},
        )

    return (f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'})
