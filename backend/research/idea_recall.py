"""Read-only tools over the *other* ideas captured for one repository.

An Ideas discussion could see its own idea in full, its assessment, its
questions, its linked wiki notes and the repo inventory — and no other idea at
all. So the model could enthusiastically design something the backlog already
contains, sometimes something already shipped, occasionally something the owner
had already decided against, and it had no way to notice. `idea_list` is the
cheap fix: the backlog is a few dozen rows, and its whole index costs a few
hundred tokens.

Named `idea_recall` rather than `ideas` so it does not grep-collide with
`backend/routes/ideas.py` forever.

Two scoping decisions, both deliberate:

- **The scope is `ideas.repo_id`, strictly** — not `discuss.idea_repo()`. That
  helper answers a different question ("which checkout can this discussion
  read?") and falls back to the registered default when a clone is not ready,
  which is right for capability and wrong here: in a single-repo setup it would
  make every repo-less idea see the entire database. It is also not a `WHERE`
  clause — it is per-idea Python involving `cloneState`.
- **A repo-less idea is its own sealed world.** It sees the other repo-less
  ideas and a repo's discussion sees none of them. This is the opposite of
  `wiki.WikiTools`' union, on purpose: an unscoped wiki note ("how do people
  solve X") is about no codebase in particular, while `backend/db/connection.py`
  says an idea with no repo is a plain product thought — and the other product
  thoughts are its world.

The idea being discussed is excluded, mirroring `LifeTools`' exclusion of the
conversation being had, and for a sharper reason: `discuss.build_context`
already carries its text at a *larger* cap than this module's, so returning it
again would hand the model a shorter copy of something it can see in full, with
no way to tell which is which.
"""
import logging

from backend.db.connection import get_db, row_to_dict
from backend.recall import (
    clip, clip_doc, excerpt, find_ci, join, limit_of, pick_one, scope_clause,
    unknown_tool, when,
)
from backend.research.idea_text import display_title

logger = logging.getLogger(__name__)

# The backlog's index, handed over whole. Same role as `wiki.WIKI_INDEX_MAX`:
# past this a ranker would be worth building, below it one is not.
IDEA_INDEX_MAX = 40

_VERDICT_WORDS = {
    'yes': 'already built',
    'partial': 'partly built already',
    'no': None,          # the common case; saying "not built" of every idea is noise
}

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'idea_list',
            'description': (
                'List the other ideas captured for this repository, with their '
                'status and whether they are already built. The idea you are '
                'discussing is above in full; this is everything else in the '
                'backlog.'
            ),
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'idea_read',
            'description': (
                'Read one of the other ideas in full by its title, as listed '
                'by idea_list — its text, its assessment, and the research '
                'notes linked to it.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {
                        'type': 'string',
                        'description': 'The idea title, as idea_list shows it.',
                    },
                },
                'required': ['title'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'idea_search',
            'description': (
                'Search the other ideas for this repository for a word or '
                'phrase. Literal text matching, not semantic.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
                'required': ['query'],
            },
        },
    },
]

TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def _body(row) -> str:
    return (row['content'] or '').strip() or (row['raw_content'] or '')


def _name(row) -> str:
    return display_title(row_to_dict(row))


class IdeaTools:
    """Bound to one repository scope and one excluded idea.

    Both are instance state rather than tool parameters, the choice
    `wiki.WikiTools` makes for its repo scope: the model has no argument to put
    another repository in.
    """

    def __init__(self, repo_id: str | None, exclude_idea_id: str | None = None):
        self.repo_id = repo_id
        self.exclude_idea_id = exclude_idea_id

    @classmethod
    def for_idea(cls, idea_id: str) -> "IdeaTools | None":
        """Scoped to the idea's own repo column, or None when there is no such
        idea — an unresolvable scope must mount no tools at all, the same rule
        `discuss.repo_root_for` follows for the code tools."""
        row = get_db().execute(
            'SELECT repo_id FROM ideas WHERE id=?', (idea_id,)
        ).fetchone()
        if row is None:
            return None
        return cls(row['repo_id'], idea_id)

    # --- scoped reads -----------------------------------------------------

    def _rows(self, limit: int = IDEA_INDEX_MAX) -> list:
        clause, params = scope_clause('repo_id', self.repo_id)
        sql = (
            'SELECT id, title, raw_content, content, status, user_verdict,'
            ' created_at, updated_at FROM ideas WHERE ' + clause
        )
        if self.exclude_idea_id:
            sql += ' AND id != ?'
            params = params + [self.exclude_idea_id]
        sql += ' ORDER BY updated_at DESC LIMIT ?'
        return get_db().execute(sql, params + [limit]).fetchall()

    def _verdicts(self, ids: list[str]) -> dict:
        """{idea_id: 'already built'} for the ones that are.

        One grouped query, never one per idea. `ideas.user_verdict` wins where
        it is set: the owner's correction beats the model's guess, which is the
        reason that column exists.
        """
        if not ids:
            return {}
        placeholders = ','.join('?' * len(ids))
        rows = get_db().execute(
            'SELECT idea_id, verdict FROM idea_assessments'
            f' WHERE idea_id IN ({placeholders})'
            ' ORDER BY assessed_at ASC, id ASC',
            ids,
        ).fetchall()
        # Ascending, so the last write per id is the newest.
        latest = {r['idea_id']: r['verdict'] for r in rows}
        return {k: v for k, v in latest.items() if v}

    # --- tools ------------------------------------------------------------

    def list_ideas(self) -> tuple[str, dict]:
        event = {'tool': 'idea_list', 'ok': True, 'count': 0}
        try:
            rows = self._rows()
            verdicts = self._verdicts([r['id'] for r in rows])
        except Exception as e:
            logger.warning('idea_list failed: %s', e)
            return ('The backlog could not be read.',
                    {**event, 'ok': False, 'error': 'read failed'})

        if not rows:
            return ('No other ideas have been captured for this repository.', event)

        blocks = []
        for r in rows:
            verdict = (r['user_verdict'] or verdicts.get(r['id']) or '').strip()
            built = _VERDICT_WORDS.get(verdict, verdict if verdict else None)
            marks = r['status'] + (f' · {built}' if built else '')
            blocks.append(f'- {_name(r)} [{marks}] — {when(r["updated_at"])}')

        return (
            'Other ideas for this repository (most recently touched first):\n'
            + join(blocks),
            {**event, 'count': len(rows)},
        )

    def read(self, title: str) -> tuple[str, dict]:
        event = {'tool': 'idea_read', 'arg': title, 'ok': True, 'count': 0}
        try:
            rows = self._rows()
        except Exception as e:
            logger.warning('idea_read failed: %s', e)
            return ('The backlog could not be read.',
                    {**event, 'ok': False, 'error': 'read failed'})

        row, reason = pick_one(rows, title, _name)
        if row is None:
            if reason == 'ambiguous':
                return (
                    f'Several ideas match "{title}". Use the full title as '
                    'idea_list shows it.',
                    {**event, 'ok': False, 'error': 'ambiguous title'},
                )
            return (
                f'No other idea here is called "{title}". Call idea_list to '
                'see what there is.',
                {**event, 'ok': False, 'error': 'not found'},
            )

        found = {**event, 'count': 1, 'title': _name(row)}
        parts = [f'# {_name(row)}',
                 f'Status: {row["status"]} · captured {when(row["created_at"])}'
                 f' · last touched {when(row["updated_at"])}']

        text, truncated = clip_doc(_body(row))
        if text.strip():
            parts.append(text + ('\n\n(Cut off here — the rest is not shown.)'
                                 if truncated else ''))

        try:
            parts += self._context_for(row['id'])
        except Exception as e:
            logger.warning('idea_read context failed: %s', e)

        return '\n\n'.join(parts), found

    def _context_for(self, idea_id: str) -> list[str]:
        """Its assessment, its research notes, and whether a plan exists."""
        from backend.research import assess, wiki

        out = []
        assessment = assess.latest_assessment(idea_id)
        if assessment:
            verdict = assessment.get('verdict')
            built = _VERDICT_WORDS.get(verdict) or f'verdict: {verdict}'
            line = f'Assessed against the code: {built}'
            confidence = assessment.get('confidence')
            if confidence:
                line += f' (confidence {confidence:.1f})'
            rationale = (assessment.get('rationale') or '').strip()
            if rationale:
                line += f'\n{clip(rationale)}'
            out.append(line)

        articles = wiki.articles_for_idea(idea_id)
        if articles:
            names = ', '.join(f'{a["slug"]} ({a["title"]})' for a in articles[:5])
            out.append(f'Research notes linked to it: {names}.'
                       ' Read any of them with wiki_read.')

        plan = get_db().execute(
            'SELECT version FROM idea_plans WHERE idea_id=?'
            ' ORDER BY version DESC LIMIT 1',
            (idea_id,),
        ).fetchone()
        if plan:
            # Named, never pasted: a plan runs to thousands of characters and
            # there is no read cap that makes including one sane.
            out.append(f'A build plan has been generated for it (v{plan["version"]}).')
        return out

    def search(self, query: str, limit: int | None = None) -> tuple[str, dict]:
        event = {'tool': 'idea_search', 'arg': query, 'ok': True, 'count': 0}
        needle = (query or '').strip()
        if not needle:
            return ('No search terms in that query.', event)

        try:
            rows = self._rows()
        except Exception as e:
            logger.warning('idea_search failed: %s', e)
            return ('That search could not be run.',
                    {**event, 'ok': False, 'error': 'search failed'})

        # `raw_content` is searched as well as `content`: a voice-captured idea
        # has its whole substance there and an empty title, so a search over
        # the polished text alone would miss exactly the ideas nobody has
        # written up yet. Matching in Python for backend/recall.find_ci's
        # reason — SQL's case folding is ASCII-only.
        hits = []
        for r in rows:
            in_title = find_ci(_name(r), needle) is not None
            body = _body(r)
            if in_title or find_ci(body, needle) is not None:
                hits.append((not in_title, r, body))
        hits.sort(key=lambda h: h[0])
        hits = hits[:limit or limit_of({})]

        if not hits:
            return (f'No other idea here mentions "{query}".', event)

        blocks = [
            f'[{_name(r)} — {r["status"]}]\n{excerpt(body, needle) if body else ""}'.rstrip()
            for _rank, r, body in hits
        ]
        return (
            f'Other ideas mentioning "{query}":\n\n' + join(blocks),
            {**event, 'count': len(hits)},
        )

    # --- dispatch ---------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if name == 'idea_list':
            return self.list_ideas()
        if name == 'idea_read':
            return self.read(str(args.get('title') or '').strip())
        if name == 'idea_search':
            return self.search(str(args.get('query') or '').strip(), limit_of(args))
        return unknown_tool(name)
