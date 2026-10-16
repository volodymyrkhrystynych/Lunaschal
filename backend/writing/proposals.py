"""The Writing discussion's two proposal tools: a new note, or an addition to one.

This is what turns a discussion into a working notebook for the book. Before
it, the only way a decision made in conversation reached the project's notes
was the Summarize button, which distils the *whole* transcript into one new
note — fine at the end of a session, useless for "yes, let's make her the
lighthouse keeper's daughter" in the middle of one.

Both tools stage a confirm card and write nothing, the Chat tab's rule
(`backend/delegate/tools.py`'s `_staged`). The accept handlers in
`backend/routes/chat.py` do the writing, through `backend/writing/notes.py`,
and they — not these tools — are the validation boundary, because the card is
editable and nothing arriving there is trusted for having been proposed.

An update defaults to **append**. A local model asked to rewrite a long note
to add one fact routinely loses three others, and the author would have to
diff the card against the note to notice. Replace exists for the case where
the author has asked for exactly that.

Bound to one project, and fails closed the way `WritingTools(None)` does: with
no project there is nothing to propose into, so every call is refused.
"""
import json

from backend.db.connection import get_db
from backend.delegate.tools import _refused, _staged
from backend.recall import pick_one, unknown_tool
from backend.writing.notes import DOC_TYPES, MAX_NOTE_CHARS, normalize_doc_type

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'propose_note',
            'description': (
                "Propose a new note for this project's notebook — a character, "
                'a piece of worldbuilding, an outline, or a general note — '
                'holding something the author has decided or worked out in this '
                'discussion. The author sees a card and confirms it; nothing is '
                'saved until they do. Use it when an idea has settled, not for '
                'every passing thought, and never for something an existing note '
                'already covers (use propose_note_update for that).'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {
                        'type': 'string',
                        'description': 'Short title, e.g. the character or place name.',
                    },
                    'doc_type': {'type': 'string', 'enum': list(DOC_TYPES)},
                    'content': {
                        'type': 'string',
                        'description': (
                            'The note itself, written as reference material the '
                            'author will reread while drafting: the facts and '
                            'decisions, not a summary of the conversation.'
                        ),
                    },
                },
                'required': ['title', 'doc_type', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_note_update',
            'description': (
                'Propose adding to an existing note in this project (by its '
                'title, as writing_list shows it) — a new fact about a character, '
                'a change to the outline. Appends by default; use mode "replace" '
                'only when the author asked for the note to be rewritten. The '
                'author confirms on a card; nothing is saved until they do.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'note': {'type': 'string', 'description': 'The note title.'},
                    'mode': {'type': 'string', 'enum': ['append', 'replace']},
                    'content': {
                        'type': 'string',
                        'description': 'The text to add (or the whole new note, for replace).',
                    },
                    'reason': {
                        'type': 'string',
                        'description': 'One line: what was decided that this records.',
                    },
                },
                'required': ['note', 'content'],
            },
        },
    },
]

TOOL_NAMES = {t['function']['name'] for t in TOOLS}


class WritingProposals:
    """Stages note cards for one project. Per run, like `ResearchTools`: the
    duplicate guard is a fact about this reply, and a shared instance would
    refuse a later reply's legitimate proposal."""

    def __init__(self, project_id: str | None):
        self.project_id = project_id
        self._seen: set[str] = set()

    def _duplicate(self, name: str, args: dict) -> bool:
        key = name + json.dumps(args, sort_keys=True, default=str)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def propose_note(self, args: dict) -> tuple[str, dict]:
        tool = 'propose_note'
        title = str(args.get('title') or '').strip()
        content = str(args.get('content') or '').strip()
        if not title:
            return _refused(tool, 'a note needs a title')
        if not content:
            return _refused(tool, 'a note needs some content')
        if len(content) > MAX_NOTE_CHARS:
            return _refused(tool, f'keep a note under {MAX_NOTE_CHARS:,} characters')
        doc_type = normalize_doc_type(args.get('doc_type'))
        existing = get_db().execute(
            'SELECT title FROM writing_context_docs WHERE project_id=?',
            (self.project_id,),
        ).fetchall()
        if any((r['title'] or '').strip().casefold() == title.casefold() for r in existing):
            return _refused(
                tool, f'a note called "{title}" already exists — use '
                'propose_note_update to add to it')
        return _staged('writing_note', tool,
                       {'title': title, 'docType': doc_type, 'content': content},
                       f'new {doc_type} note "{title}"')

    def propose_note_update(self, args: dict) -> tuple[str, dict]:
        tool = 'propose_note_update'
        wanted = str(args.get('note') or args.get('title') or '').strip()
        content = str(args.get('content') or '').strip()
        mode = str(args.get('mode') or 'append').strip().lower()
        if mode not in ('append', 'replace'):
            mode = 'append'
        if not content:
            return _refused(tool, 'nothing to add')
        if len(content) > MAX_NOTE_CHARS:
            return _refused(tool, f'keep it under {MAX_NOTE_CHARS:,} characters')
        rows = get_db().execute(
            'SELECT id, title, doc_type, updated_at FROM writing_context_docs'
            ' WHERE project_id=?',
            (self.project_id,),
        ).fetchall()
        hit, reason = pick_one(rows, wanted, lambda r: r['title'])
        if hit is None:
            if reason == 'ambiguous':
                return _refused(tool, f'several notes match "{wanted}"; use the full title')
            return _refused(tool, f'no note called "{wanted}" — check writing_list, '
                                  'or use propose_note for a new one')
        data = {
            'noteId': hit['id'], 'title': hit['title'], 'docType': hit['doc_type'],
            'mode': mode, 'content': content,
            'reason': str(args.get('reason') or '').strip(),
            # The raw unix time, compared at accept: an edit made after this
            # card was staged makes the card stale rather than overwritten.
            'baseUpdatedAt': hit['updated_at'],
        }
        verb = 'rewrite of' if mode == 'replace' else 'addition to'
        return _staged('writing_note_update', tool, data, f'{verb} "{hit["title"]}"')

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if name not in TOOL_NAMES:
            return unknown_tool(name)
        if not self.project_id:
            return _refused(name, 'this discussion is not attached to a project')
        if self._duplicate(name, args):
            return 'That exact proposal is already staged during this reply.', {
                'tool': name, 'arg': args, 'ok': False,
                'error': 'duplicate tool call ignored',
            }
        if name == 'propose_note':
            return self.propose_note(args)
        return self.propose_note_update(args)
