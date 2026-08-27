"""The LLM wiki: agent-written articles, with an audit trail.

Three decisions are load-bearing:

**Writes are copy-on-write.** Before any edit the *previous* title and content
go into wiki_revisions with a unified diff, then `revision` increments. The
agent is a background process editing prose the user relies on; being able to
see and undo what it did is what makes that acceptable. A `locked` article is
the user's — the agent is told to propose changes in its answer instead.

**Retrieval hands the model the whole index, not just a retriever.** At a few
dozen articles, `list_articles()` returning {slug, title, summary} for
everything costs ~1,200 tokens and lets the model pick by name, which beats any
ranking function and costs one query. FTS is the fallback above WIKI_INDEX_MAX,
and the escape hatch for re-querying with different words after a miss.
Embeddings are deliberately not used: the `embed` alias has ctx-size 2048 so an
article would need chunking, and its vectors are frozen because learning_cards
depends on them. Revisit above ~300 articles.

**An article belongs to a repository, or to none.** `repo_id IS NULL` means a
research note about a problem space — the only kind that existed before, and
still the right shape for "how do other people do spaced repetition". A
`repo_id` means a note about one module of one codebase. Slugs are unique per
repo, not globally: two codebases will both have something worth calling
`scheduling`, and one silently overwriting the other is the bug that shape
prevents. Retrieval is always scoped, because handing an agent another repo's
notes about a same-named module is worse than handing it nothing.
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

# Enough to cover an index of both kinds for one repo; FTS takes over above it.
FTS_OVERFETCH = 4


class ArticleLocked(PermissionError):
    """The user owns this article; the agent may not overwrite it."""


def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (text or '').strip().lower()).strip('-')
    return slug[:80] or 'untitled'


def _repo_clause(repo_id: str | None) -> tuple[str, list]:
    """SQL for "belongs to this repo", where None means the unscoped notes.

    `repo_id = NULL` is never true in SQL, so the two cases genuinely need
    different operators — writing this once is how a caller stops getting it
    wrong and silently listing nothing.
    """
    if repo_id is None:
        return 'repo_id IS NULL', []
    return 'repo_id = ?', [repo_id]


def get_article(slug: str, repo_id: str | None = None) -> dict | None:
    clause, params = _repo_clause(repo_id)
    row = get_db().execute(
        f'SELECT * FROM wiki_articles WHERE slug=? AND {clause}',
        [slug, *params],
    ).fetchone()
    return row_to_dict(row) if row else None


def get_article_by_id(article_id: str) -> dict | None:
    row = get_db().execute(
        'SELECT * FROM wiki_articles WHERE id=?', (article_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def list_articles(
    limit: int = WIKI_INDEX_MAX,
    repo_id: str | None = None,
    kind: str | None = None,
) -> list[dict]:
    """The index: enough to choose an article, cheap enough to inline."""
    clause, params = _repo_clause(repo_id)
    if kind:
        clause += ' AND kind = ?'
        params = [*params, kind]
    rows = get_db().execute(
        'SELECT id, slug, title, summary, kind, revision, locked, updated_at'
        f' FROM wiki_articles WHERE {clause} ORDER BY updated_at DESC LIMIT ?',
        [*params, limit],
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def search_articles(query: str, limit: int = 5, repo_id: str | None = None) -> list[dict]:
    """FTS-ranked articles, in rank order, scoped to one repo.

    The scope is applied when hydrating the FTS hits rather than inside the
    MATCH: `wiki_fts` is an external-content table over title/summary/content/
    tags and has no repo column to filter on. Over-fetching first keeps a repo
    with few articles from coming back empty because the top hits all belonged
    to another one.
    """
    hits = search_wiki_fts(query, limit * FTS_OVERFETCH)
    if not hits:
        return []
    clause, params = _repo_clause(repo_id)
    db = get_db()
    placeholders = ','.join('?' * len(hits))
    by_id = {}
    for row in db.execute(
        'SELECT id, slug, title, summary, kind FROM wiki_articles'
        f' WHERE id IN ({placeholders}) AND {clause}',
        [*[h['id'] for h in hits], *params],
    ).fetchall():
        by_id[row['id']] = row_to_dict(row)
    ordered = [by_id[h['id']] for h in hits if h['id'] in by_id]
    return ordered[:limit]


def upsert_article(
    slug: str,
    title: str,
    summary: str,
    content: str,
    *,
    repo_id: str | None = None,
    kind: str = 'research',
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

    clause, params = _repo_clause(repo_id)
    existing = db.execute(
        f'SELECT * FROM wiki_articles WHERE slug=? AND {clause}',
        [slug, *params],
    ).fetchone()
    sources_json = json.dumps(sources) if sources else None

    if existing is None:
        article_id = str(ULID())
        db.execute(
            'INSERT INTO wiki_articles(id, repo_id, slug, title, summary, content,'
            ' sources, tags, kind, revision, locked, last_researched_at,'
            ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,0,?,?,?)',
            (article_id, repo_id, slug, title.strip(), summary, content,
             sources_json, tags_json(tags), kind, now, now, now),
        )
        db.execute(
            'INSERT INTO wiki_revisions(id, article_id, revision, title, content,'
            ' diff, author, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (str(ULID()), article_id, 1, title.strip(), content, None, author,
             note or 'created', now),
        )
        db.commit()
        return get_article(slug, repo_id)

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
    return get_article(slug, repo_id)


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
        'SELECT a.id, a.slug, a.title, a.summary, a.kind, l.relevance'
        ' FROM idea_wiki_links l JOIN wiki_articles a ON a.id = l.article_id'
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
                'List every article in the wiki with its one-line summary. Use '
                'this first to see what is already known.'
            ),
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'wiki_search',
            'description': 'Search the wiki by keyword.',
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


class WikiTools:
    """Repo-scoped wiki tools.

    Same duck type as CodeTools, and for the same reason: agent._loop calls
    `dispatch[name].run_tool(...)`, so binding a repo means binding an object
    rather than threading an argument through the shared loop.

    A repo's agent sees that repo's articles *and* the unscoped research notes:
    "how do other people solve this" is not about any one codebase, and hiding
    it would make every repo re-research the same problem space.
    """

    def __init__(self, repo_id: str | None = None):
        self.repo_id = repo_id

    def _visible(self, limit: int = WIKI_INDEX_MAX) -> list[dict]:
        articles = list_articles(limit, repo_id=self.repo_id)
        if self.repo_id is not None:
            articles += list_articles(limit, repo_id=None)
        return articles

    def _find(self, slug: str) -> dict | None:
        article = get_article(slug, self.repo_id)
        if article is None and self.repo_id is not None:
            article = get_article(slug, None)
        return article

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        """Execute a wiki tool call. Never raises, like web.run_tool."""
        if name == 'wiki_list':
            articles = self._visible()
            if not articles:
                return ('The wiki is empty.', {'tool': 'wiki_list', 'ok': True, 'count': 0})
            return ('\n'.join(_line(a) for a in articles),
                    {'tool': 'wiki_list', 'ok': True, 'count': len(articles)})

        if name == 'wiki_search':
            query = (args.get('query') or '').strip()
            hits = search_articles(query, repo_id=self.repo_id)
            if self.repo_id is not None:
                hits += [h for h in search_articles(query, repo_id=None)
                         if h['id'] not in {x['id'] for x in hits}]
            if not hits:
                return (f'No wiki articles match "{query}".',
                        {'tool': 'wiki_search', 'arg': query, 'ok': True, 'count': 0})
            return ('\n'.join(_line(a) for a in hits),
                    {'tool': 'wiki_search', 'arg': query, 'ok': True, 'count': len(hits)})

        if name == 'wiki_read':
            slug = (args.get('slug') or '').strip()
            article = self._find(slug)
            if not article:
                return (f'No wiki article with slug "{slug}".',
                        {'tool': 'wiki_read', 'arg': slug, 'ok': False})
            return (f"# {article['title']}\n\n{article['content']}",
                    {'tool': 'wiki_read', 'arg': slug, 'ok': True,
                     'title': article['title']})

        return (f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'})


def _line(article: dict) -> str:
    return f"- {article['slug']}: {article['title']} — {article.get('summary') or ''}"


# The unscoped instance, for the callers that have no repo in hand.
_UNSCOPED = WikiTools()


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Module-level dispatch over the unscoped wiki, kept so agent._DISPATCH
    (which maps a name to a *module*) keeps working unchanged."""
    return _UNSCOPED.run_tool(name, args)
