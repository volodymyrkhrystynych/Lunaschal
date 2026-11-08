"""What the coding agent can look up, served on its model socket.

The container is `--network none`, so the agent cannot reach the library, the
repo's wiki or the web itself. It reaches *this* instead: a pi extension
(agent/config/pi/extensions/lunaschal.js) calls `/lunaschal/tools` on the same
socket the model calls go through, and the host runs the lookup with the
toolboxes the rest of the app already has. The container gains no network; the
host does the reaching, through the same SSRF-guarded fetch everything else uses.

**This endpoint is the boundary, not the extension.** The agent owns `$HOME`,
so it can rewrite the extension or curl the socket directly with anything it
likes. So the names are checked against what this task was offered, every
argument is clipped, every result is clipped, and each task has a budget — a
looping agent can spend its lookups, not the user's Tavily quota or an evening
of GPU time.

Web access is a deliberate exception to "unattended work gets no web"
(backend/research/CLAUDE.md), made because the user asked for it. It keeps the
Chat tab's offline-first gate — library tools and the delegate share one
`ResearchTools`, so the web is refused until the library has been searched —
and it searches through **Tavily with the code agent's own key**, never the
app's configured provider (`web.search_override`).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from backend.ai import service

logger = logging.getLogger(__name__)

MAX_LOOKUPS = 200
MAX_WEB = 20
MAX_RESULT_CHARS = 16_000
MAX_ARG_CHARS = 500

EventFn = Callable[[str, dict], None]

_QUERY = {'type': 'string', 'description': 'What to look for.'}

TOOLS: dict[str, dict] = {
    'library_search': {
        'description': (
            "Search the user's offline reference library: library and framework API "
            'documentation packages (versioned, e.g. flask 3.1 or react 19 docs), '
            'DevDocs, Stack Overflow and encyclopedias. Use this first for any '
            'question about how a library, API or tool works. Give one to four '
            'complementary queries; name the library in them to reach its docs. '
            'Results are candidates: read the best one or two with library_read '
            'before relying on them.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {'queries': {'type': 'array', 'items': {'type': 'string'},
                                       'minItems': 1, 'maxItems': 4}},
            'required': ['queries'],
        },
    },
    'library_read': {
        'description': 'Read one article returned by library_search, by its archiveId and path.',
        'parameters': {
            'type': 'object',
            'properties': {'archiveId': {'type': 'string'}, 'path': {'type': 'string'}},
            'required': ['archiveId', 'path'],
        },
    },
    'web_research': {
        'description': (
            'Hand a question to a web researcher, who searches, reads pages and '
            'returns a summary with sources. Slow (minutes) and budgeted: use it '
            'only after library_search and library_read did not answer, or for '
            'something inherently current (a release that came out recently, an '
            'open issue). Write the question out in full; the researcher cannot '
            'see this repository or your conversation.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'question': {'type': 'string'},
                'current': {'type': 'boolean', 'description': (
                    'True only when the question is inherently about now, which '
                    'lets it skip the library.')},
            },
            'required': ['question'],
        },
    },
    'repo_wiki_search': {
        'description': (
            "Search this repository's wiki: notes written nightly about each module "
            '(what it does, how it fits together) plus research notes. Faster than '
            'reading code when you need the shape of an unfamiliar area.'
        ),
        'parameters': {'type': 'object', 'properties': {'query': _QUERY}, 'required': ['query']},
    },
    'repo_wiki_read': {
        'description': 'Read one wiki article by the slug repo_wiki_search returned.',
        'parameters': {'type': 'object', 'properties': {'slug': {'type': 'string'}},
                       'required': ['slug']},
    },
    'code_map': {
        'description': (
            "Look a concept up in this repository's code graph and get back the "
            'symbols and files involved, with line numbers. Cheaper than grepping '
            'when you do not yet know what things are called. The graph is of the '
            'main branch, not of your edits.'
        ),
        'parameters': {'type': 'object', 'properties': {'query': _QUERY}, 'required': ['query']},
    },
}

_KNOWLEDGE_NAMES = {'library_search': 'local_knowledge_search',
                    'library_read': 'local_knowledge_read'}
_WIKI_NAMES = {'repo_wiki_search': 'wiki_search', 'repo_wiki_read': 'wiki_read'}


def _clip(value, limit: int = MAX_ARG_CHARS) -> str:
    return str(value or '').strip()[:limit]


def _clip_result(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + '\n… (clipped; read a narrower piece if you need more)'


def _code_agent_settings() -> dict:
    from backend.ai.provider import get_settings
    s = get_settings() or {}
    return {'web': bool(s.get('code_agent_web', 1)),
            'tavily_key': (s.get('code_agent_tavily_key') or '').strip()}


class Lookups:
    """One task's lookups. Per-task because the budgets and the offline-first
    gate are per-task facts: a shared instance would let one task's library
    search unlock the web for the next."""

    def __init__(self, repo_id: str | None, on_event: EventFn | None = None,
                 settings: dict | None = None):
        from backend.delegate.research_tools import ResearchTools
        from backend.research.agent import make_checkpoint

        self.repo_id = repo_id
        self.on_event = on_event or (lambda kind, payload: None)
        self.settings = settings if settings is not None else _code_agent_settings()
        # Set when the task's proxy stops, so a web run still going when the
        # agent is cancelled stops at its next step instead of at its deadline.
        self.stopped = threading.Event()
        self.research = ResearchTools(checkpoint=make_checkpoint(self.stopped))
        self.lookups = 0
        self.web_runs = 0
        self._lock = threading.Lock()
        self.names = self._available()

    # ------------------------------------------------------------ offering

    def _repo_root(self) -> Path | None:
        from backend.repos import registry
        try:
            return registry.repo_root(self.repo_id)
        except Exception:
            logger.exception('code agent: could not resolve repo %s', self.repo_id)
            return None

    def _available(self) -> list[str]:
        """Degrade by absence: a tool that can only answer "unavailable"
        spends a turn to learn nothing (the `code.tools_for` rule)."""
        names = ['library_search', 'library_read']
        if self.settings.get('web') and self.settings.get('tavily_key'):
            names.append('web_research')
        if self.repo_id:
            from backend.research import wiki
            if wiki.list_articles(1, repo_id=self.repo_id, kind=wiki.WORK_KINDS):
                names += ['repo_wiki_search', 'repo_wiki_read']
        root = self._repo_root()
        if root is not None:
            from backend.repos import graph
            if graph.has_graph(root) and graph.graphify_bin():
                names.append('code_map')
        return names

    def offered(self) -> list[dict]:
        return [{'name': n, **TOOLS[n]} for n in self.names]

    # ------------------------------------------------------------- calling

    def call(self, name: str, args: dict) -> tuple[int, dict]:
        """(HTTP status, {text, ok}). Never raises: a failed lookup is a
        result the agent reads, not a crash in its tool."""
        if name not in self.names:
            return 404, {'text': f'No lookup tool named {name!r}.', 'ok': False}
        args = args if isinstance(args, dict) else {}
        with self._lock:
            if self.lookups >= MAX_LOOKUPS:
                return 200, {'text': 'The lookup budget for this task is spent. '
                                     'Work with what you have.', 'ok': False}
            if name == 'web_research' and self.web_runs >= MAX_WEB:
                return 200, {'text': 'The web research budget for this task is spent. '
                                     'Use the library or work with what you have.', 'ok': False}
            self.lookups += 1
        started = time.monotonic()
        try:
            text, event = self._run(name, args)
        except Exception as e:
            logger.exception('code agent lookup %s failed', name)
            text, event = f'{name} failed: {e}', {'ok': False, 'error': str(e)}
        payload = {
            'action': 'lookup', 'tool': name, 'arg': event.get('arg'),
            'ok': bool(event.get('ok')), 'count': event.get('count'),
            'seconds': round(time.monotonic() - started, 2),
        }
        if event.get('blocked'):
            payload['blocked'] = True
        if event.get('sources'):
            payload['sources'] = [
                {'title': s.get('title'), 'url': s.get('url')}
                for s in event['sources'][:10] if isinstance(s, dict)
            ]
        self.on_event('proxy', payload)
        return 200, {'text': _clip_result(text or ''), 'ok': bool(event.get('ok'))}

    def _run(self, name: str, args: dict) -> tuple[str, dict]:
        if name == 'library_search':
            queries = args.get('queries')
            if not isinstance(queries, list):
                queries = [args.get('query')]
            queries = [_clip(q) for q in queries if _clip(q)][:4]
            if not queries:
                return 'library_search needs at least one query.', {'ok': False}
            text, event = self.research.run_tool('local_knowledge_search', {'queries': queries})
            return text, {**event, 'arg': ' | '.join(queries)}
        if name == 'library_read':
            text, event = self.research.run_tool('local_knowledge_read', {
                'archiveId': _clip(args.get('archiveId')), 'path': _clip(args.get('path'), 2000)})
            return text, {**event, 'arg': _clip(args.get('path'), 200)}
        if name == 'web_research':
            return self._web(args)
        if name in _WIKI_NAMES:
            from backend.research.wiki import WikiTools
            key = 'query' if name == 'repo_wiki_search' else 'slug'
            return WikiTools(self.repo_id).run_tool(_WIKI_NAMES[name], {key: _clip(args.get(key))})
        if name == 'code_map':
            from backend.research.code import CodeTools
            root = self._repo_root()
            if root is None:
                return 'The code map is not available right now.', {'ok': False}
            return CodeTools(root).code_map(_clip(args.get('query')))
        return f'No lookup tool named {name!r}.', {'ok': False}

    def _web(self, args: dict) -> tuple[str, dict]:
        from backend.research import web
        question = _clip(args.get('question'), 2000)
        if not question:
            return 'web_research needs a question.', {'ok': False}
        reason = 'current' if args.get('current') is True else 'local_insufficient'
        with self._lock:
            self.web_runs += 1
        # P2 and preemptible: the researcher's own model turns must yield to a
        # chat message exactly as the agent's do. The search provider is a
        # property of this call stack, so the delegate, its loop and the deep
        # pass all search through Tavily without being told.
        with service.background(), web.search_override('tavily', self.settings['tavily_key']):
            text, event = self.research.run_tool('delegate', {'task': question, 'reason': reason})
        if event.get('blocked'):
            with self._lock:
                self.web_runs -= 1  # refused by the gate, so nothing was spent
            # The gate's own wording names the Chat tab's tools (delegate,
            # local_knowledge_search, reason=current); say it in this agent's.
            text = ('Refused: search the offline library first. Call library_search'
                    + (' and read the best result with library_read' if self.research.searched else '')
                    + ', then call web_research again if it did not answer. For a question that '
                    'is inherently about now, call web_research with current: true.')
        return text or 'The web research came back empty.', {**event, 'arg': question[:200]}
