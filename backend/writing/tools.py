"""Read-only tools over one writing project: its chapters and its notes.

A Writing discussion could previously see exactly what the author ticked in the
context panel, pasted whole into the system prompt — and chapters were not
tickable at all. The prompt did not even list what else existed, so the model's
working assumption was that what it had been handed *was* the project: it would
invent a detail the story had already settled, or ask the author to paste in
something it could have read.

The checkbox panel stays. Ticking a note means "this matters for every turn of
this conversation", which is a different claim from "this is reachable" — these
tools are for everything the author did not tick.

Same shape as `backend/lifewiki/tools.py` (the user's own record) and
`backend/research/idea_recall.py` (a repository's other ideas): the scope is
bound to the instance, the caps come from `backend/recall.py`, and
`run_tool(name, args) -> (text, event)` is the duck type
`backend/research/agent.py`'s loop dispatches to.
"""
import logging

from backend.db.connection import get_db
from backend.recall import (
    clip, clip_doc, excerpt, find_ci, join, limit_of, pick_one, unknown_tool,
)

logger = logging.getLogger(__name__)

# Prose averages close enough to this for the only decision a word count here
# informs: is that a stub, a scene, or a whole chapter?
CHARS_PER_WORD = 6

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'writing_list',
            'description': (
                'List every chapter and note in this project — chapters in '
                'order, notes by type — with an approximate length for each. '
                'Use it first to see what the author has already written. '
                'Anything they attached to this conversation is already above; '
                'this is everything else.'
            ),
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'writing_read',
            'description': (
                'Read one chapter or note in full by its title, as listed by '
                'writing_list. Use it when the answer turns on what the text '
                'actually says rather than on what it is called.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {
                        'type': 'string',
                        'description': 'The chapter or note title, exactly as listed.',
                    },
                },
                'required': ['title'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'writing_search',
            'description': (
                "Search this project's chapters and notes for a word or "
                'phrase — a character name, a place, a detail the author has '
                'used before. Literal text matching, not semantic: search for '
                'words that would actually appear on the page.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                },
                'required': ['query'],
            },
        },
    },
]

TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def _words(chars: int) -> str:
    if not chars:
        return 'empty'
    return f'~{max(1, chars // CHARS_PER_WORD):,} words'


class WritingTools:
    """Bound to one project. An unset scope means *nothing*, not everything.

    That is the opposite of `WikiTools(repo_id=None)`, where unscoped notes are
    deliberately visible to every repo — so it is worth being explicit. Two
    things enforce it: the tools are never mounted without a resolved project
    (`backend/delegate/chat.py`'s `_toolbox`), and every query below binds
    `project_id = ?`, which fails closed because `project_id = NULL` is never
    true in SQL. An instance built with None therefore returns zero rows rather
    than every project's chapters.
    """

    def __init__(self, project_id: str | None):
        self.project_id = project_id

    # --- reads ------------------------------------------------------------

    def _chapters(self, columns: str = 'id, title, position') -> list:
        return get_db().execute(
            f'SELECT {columns} FROM writing_chapters'
            ' WHERE project_id = ? ORDER BY position ASC',
            (self.project_id,),
        ).fetchall()

    def _notes(self, columns: str = 'id, title, doc_type') -> list:
        return get_db().execute(
            f'SELECT {columns} FROM writing_context_docs'
            ' WHERE project_id = ? ORDER BY created_at ASC',
            (self.project_id,),
        ).fetchall()

    def list_all(self) -> tuple[str, dict]:
        event = {'tool': 'writing_list', 'ok': True, 'count': 0}
        try:
            # LENGTH() rather than the column: SQLite answers it without
            # materialising the prose, so a 200k-character project is still
            # one cheap query.
            chapters = self._chapters('id, title, position, LENGTH(content) AS chars')
            notes = self._notes('id, title, doc_type, LENGTH(content) AS chars')
        except Exception as e:
            logger.warning('writing_list failed: %s', e)
            return ('That project could not be read.',
                    {**event, 'ok': False, 'error': 'read failed'})

        if not chapters and not notes:
            return ('This project has no chapters or notes yet.', event)

        parts = []
        if chapters:
            lines = [
                f'- {i}. {r["title"] or "Untitled"} — {_words(r["chars"] or 0)}'
                for i, r in enumerate(chapters, start=1)
            ]
            parts.append('Chapters, in order:\n' + '\n'.join(lines))
        if notes:
            lines = [
                f'- [{r["doc_type"] or "note"}] {r["title"] or "Untitled"}'
                f' — {_words(r["chars"] or 0)}'
                for r in notes
            ]
            parts.append('Notes:\n' + '\n'.join(lines))

        return (join(parts), {**event, 'count': len(chapters) + len(notes)})

    def read(self, title: str) -> tuple[str, dict]:
        event = {'tool': 'writing_read', 'arg': title, 'ok': True, 'count': 0}
        try:
            rows = [
                ('chapter', r) for r in
                self._chapters('id, title, position, content')
            ] + [
                (r['doc_type'] or 'note', r) for r in
                self._notes('id, title, doc_type, content')
            ]
        except Exception as e:
            logger.warning('writing_read failed: %s', e)
            return ('That project could not be read.',
                    {**event, 'ok': False, 'error': 'read failed'})

        hit, reason = pick_one(rows, title, lambda pair: pair[1]['title'])
        if hit is None:
            if reason == 'ambiguous':
                return (
                    f'Several things here match "{title}". Use the full title '
                    'as writing_list shows it.',
                    {**event, 'ok': False, 'error': 'ambiguous title'},
                )
            return (
                f'No chapter or note called "{title}". Call writing_list to '
                'see what there is.',
                {**event, 'ok': False, 'error': 'not found'},
            )

        kind, row = hit
        body = row['content'] or ''
        name = row['title'] or 'Untitled'
        found = {**event, 'count': 1, 'title': name,
                 'kind': 'chapter' if kind == 'chapter' else 'note'}

        if not body.strip():
            return (f'"{name}" is empty — nothing has been written in it yet.', found)

        text, truncated = clip_doc(body)
        header = (f'# Chapter {row["position"] + 1}: {name}' if kind == 'chapter'
                  else f'# {kind}: {name}')
        out = f'{header}\n\n{text}'
        if truncated:
            out += (
                f'\n\n(Cut off here — {len(text):,} of {len(body):,} characters '
                'shown. Use writing_search to find a specific passage further in.)'
            )
        return out, found

    def search(self, query: str, limit: int | None = None) -> tuple[str, dict]:
        event = {'tool': 'writing_search', 'arg': query, 'ok': True, 'count': 0}
        needle = (query or '').strip()
        if not needle:
            return ('No search terms in that query.', event)

        try:
            rows = [
                ('chapter', r) for r in
                self._chapters('id, title, position, content')
            ] + [
                (r['doc_type'] or 'note', r) for r in
                self._notes('id, title, doc_type, content')
            ]
        except Exception as e:
            logger.warning('writing_search failed: %s', e)
            return ('That search could not be run.',
                    {**event, 'ok': False, 'error': 'search failed'})

        # Matching in Python, not in SQL: SQLite's LIKE folds case for ASCII
        # only, so a Ukrainian or Polish name would match at one casing and not
        # the other — invisibly. See backend/recall.find_ci.
        hits = []
        for kind, row in rows:
            in_title = find_ci(row['title'] or '', needle) is not None
            at = find_ci(row['content'] or '', needle)
            if in_title or at is not None:
                hits.append((not in_title, kind, row))
        # Title matches first: a hit on the name of a thing is almost always
        # the one wanted. Within each group the query's own order (chapters by
        # position, then notes) is kept — a stable, explainable order, since
        # there is no relevance score to sort by.
        hits.sort(key=lambda h: h[0])
        hits = hits[:limit or limit_of({})]

        if not hits:
            return (f'Nothing in this project matches "{query}".', event)

        blocks = []
        for _rank, kind, row in hits:
            name = row['title'] or 'Untitled'
            label = (f'chapter {row["position"] + 1} — {name}' if kind == 'chapter'
                     else f'{kind} — {name}')
            body = row['content'] or ''
            snippet = excerpt(body, needle) if body else clip(name)
            blocks.append(f'[{label}]\n{snippet}')

        return (
            f'In this project, matching "{query}":\n\n' + join(blocks),
            {**event, 'count': len(hits)},
        )

    # --- dispatch ---------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if name == 'writing_list':
            return self.list_all()
        if name == 'writing_read':
            return self.read(str(args.get('title') or '').strip())
        if name == 'writing_search':
            return self.search(str(args.get('query') or '').strip(), limit_of(args))
        return unknown_tool(name)
