"""Search and read the source work a fanfic is set in, scoped to one project.

A canon source is a Fandom (MediaWiki) wiki imported from its XML dump by
`backend/writing/canon_import.py` into the docpack file format, and linked to
any number of writing projects. The file format is shared with the Knowledge
tab's docpacks so the BM25 search and the neighbour-window read are one
implementation (`docpacks.search_file` / `read_file`) — but the *registry* is
not shared: an installed docpack is searched by every chat, and a wiki about
one fandom has no business answering a question in the Chat tab or in an Ideas
discussion about a codebase.

Fails closed like `WritingTools(None)`: no project, or a project with no ready
source linked, sees nothing — and `backend/delegate/chat.py` does not mount
these tools at all in that case, so the prompt never promises a search that
can only answer "nothing found".

Every read carries an attribution line. Fandom content is CC BY-SA, and a note
that the author later builds out of a canon read should say where it came from.
"""
import logging
import os
from pathlib import Path

from backend.db.connection import get_db
from backend.offline_knowledge import docpacks
from backend.recall import clip, join, limit_of, unknown_tool

logger = logging.getLogger(__name__)

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'canon_search',
            'description': (
                "Search the original work's wiki (the canon this story is set "
                'in) for a character, place, event or term. Keyword search: use '
                'the names that would appear on the wiki page. Returns ids to '
                'open with canon_read.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'canon_read',
            'description': (
                'Read one canon wiki section, with the rest of its page around '
                'it, by the id canon_search returned. Read before relying on a '
                'detail — a search snippet is not the page.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'id': {'type': 'string'}},
                'required': ['id'],
            },
        },
    },
]

TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def canon_root() -> Path:
    return Path(os.environ.get('CANON_ROOT', './data/canon')).expanduser().resolve()


def path_for(source: dict) -> Path:
    return canon_root() / source['filename']


def ready_sources(project_id: str | None) -> list[dict]:
    """The project's linked sources whose file is actually there to search."""
    if not project_id:
        return []
    rows = get_db().execute(
        'SELECT s.* FROM writing_canon_sources s'
        ' JOIN writing_project_canon pc ON pc.source_id = s.id'
        " WHERE pc.project_id = ? AND s.status = 'ready' AND s.filename IS NOT NULL"
        ' ORDER BY s.name',
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows if path_for(dict(r)).is_file()]


def attribution(source: dict) -> str:
    where = source.get('wiki_host') or source['name']
    return f'Source: {where} (Fandom wiki), {source.get("licence") or "CC BY-SA"}.'


class CanonTools:
    def __init__(self, project_id: str | None):
        self.project_id = project_id
        self._sources = {s['id']: s for s in ready_sources(project_id)}

    @property
    def available(self) -> bool:
        return bool(self._sources)

    def search(self, query: str, limit: int) -> tuple[str, dict]:
        event = {'tool': 'canon_search', 'arg': query, 'ok': True, 'count': 0}
        query = (query or '').strip()
        if not query:
            return 'No search terms in that query.', event
        if not self._sources:
            return 'No canon source is linked to this project.', {
                **event, 'ok': False, 'error': 'no canon'}
        blocks = []
        for source in self._sources.values():
            try:
                hits = docpacks.search_file(path_for(source), query, limit)
            except Exception as e:
                logger.warning('canon_search on %s failed: %s', source['id'], e)
                continue
            label = source['name'] if len(self._sources) > 1 else ''
            for path, title, snippet in hits:
                prefix = f'{label}: ' if label else ''
                blocks.append(f'[{source["id"]}:{path}] {prefix}{title}\n{clip(snippet)}')
        blocks = blocks[:limit]
        if not blocks:
            return f'Nothing in the canon wiki matches "{query}".', event
        return (f'Canon wiki, matching "{query}":\n\n' + join(blocks),
                {**event, 'count': len(blocks)})

    def read(self, ref: str) -> tuple[str, dict]:
        event = {'tool': 'canon_read', 'arg': ref, 'ok': True, 'count': 0}
        source_id, _, chunk = (ref or '').strip().strip('[]').partition(':')
        source = self._sources.get(source_id)
        if source is None or not chunk:
            return ('That is not an id canon_search returned. Search first, then '
                    'pass the id in brackets exactly.',
                    {**event, 'ok': False, 'error': 'unknown id'})
        try:
            found = docpacks.read_file(path_for(source), chunk)
        except LookupError:
            return ('No such section in the canon wiki.',
                    {**event, 'ok': False, 'error': 'not found'})
        except Exception as e:
            logger.warning('canon_read %s failed: %s', ref, e)
            return ('The canon wiki could not be read.',
                    {**event, 'ok': False, 'error': 'read failed'})
        text = f'# {found["title"]}\n\n{found["text"]}\n\n{attribution(source)}'
        return text, {**event, 'count': 1, 'title': found['title']}

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if name == 'canon_search':
            return self.search(str(args.get('query') or ''), limit_of(args))
        if name == 'canon_read':
            return self.read(str(args.get('id') or ''))
        return unknown_tool(name)
