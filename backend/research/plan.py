"""Turning a developed idea into a spec a coding agent can execute.

`render_plan_markdown` is pure — no model, no DB — so the model only has to
produce structure and never formatting. That split also means the deterministic
sections (what already exists, which decisions are settled, which are open) are
stitched in by Python from real rows rather than paraphrased, for the same
reason the repo inventory is extracted rather than summarized.
"""
import json
import logging
import re
import time

from ulid import ULID

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.db.connection import get_db, row_to_dict

logger = logging.getLogger(__name__)

MAX_TOKENS = 8192

SYSTEM_PROMPT = """You write implementation specs for Lunaschal, a single-user, \
local-first life-management app (Flask + SQLite backend, React 19 + Vite \
frontend, local llama.cpp inference).

Your reader is a coding agent that will implement this without talking to you. \
Write for that reader:
- Name real files and real conventions from the inventory you are given. \
"Add a blueprint in backend/routes/ and register it in app.py" beats "add an \
endpoint".
- Prefer extending what exists to adding something parallel to it.
- Be specific about data: name tables and columns.
- Technical considerations are where you put the things that would otherwise be \
learned the hard way — concurrency, migration order, what breaks if this is \
done naively.
- Keep every list tight. A spec nobody reads is worse than a short one."""

_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'goals': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}},
        'nonGoals': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}},
        'dataModel': {
            'type': 'array', 'maxItems': 8,
            'items': {
                'type': 'object',
                'properties': {
                    'table': {'type': 'string'},
                    'purpose': {'type': 'string'},
                    'columns': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string'}},
                },
                'required': ['table', 'purpose'],
            },
        },
        'api': {
            'type': 'array', 'maxItems': 12,
            'items': {
                'type': 'object',
                'properties': {
                    'method': {'type': 'string'},
                    'path': {'type': 'string'},
                    'purpose': {'type': 'string'},
                },
                'required': ['method', 'path'],
            },
        },
        'frontend': {
            'type': 'array', 'maxItems': 12,
            'items': {
                'type': 'object',
                'properties': {
                    'file': {'type': 'string'},
                    'purpose': {'type': 'string'},
                },
                'required': ['file'],
            },
        },
        'technicalConsiderations': {
            'type': 'array', 'maxItems': 8,
            'items': {
                'type': 'object',
                'properties': {
                    'topic': {'type': 'string'},
                    'note': {'type': 'string'},
                },
                'required': ['topic', 'note'],
            },
        },
        'phases': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}},
        'risks': {
            'type': 'array', 'maxItems': 6,
            'items': {
                'type': 'object',
                'properties': {
                    'risk': {'type': 'string'},
                    'mitigation': {'type': 'string'},
                },
                'required': ['risk'],
            },
        },
        'testPlan': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string'}},
    },
    'required': ['summary', 'goals', 'dataModel', 'api', 'frontend',
                 'technicalConsiderations', 'phases', 'testPlan'],
}


def _bullets(items, prefix='- '):
    return [f'{prefix}{item}' for item in items if item]


# The model returns phases already numbered about half the time ("1. Database:
# add the tables"), and the renderer numbers them too, so the plan came out as
# "1. 1. Database:". The separator is required, so "2FA rollout" keeps its number.
_LEADING_ENUMERATOR = re.compile(
    r'^\s*(?:phase|step)?\s*\d+\s*[.)\]:—–-]\s+', re.IGNORECASE
)


def _unnumbered(phase: str) -> str:
    """A phase with any enumerator the model supplied stripped off."""
    return _LEADING_ENUMERATOR.sub('', phase or '').strip()


def render_plan_markdown(
    title: str,
    spec: dict,
    *,
    evidence: list[dict] | None = None,
    answered: list[dict] | None = None,
    open_questions: list[dict] | None = None,
    sources: list[dict] | None = None,
) -> str:
    """Pure rendering. Missing sections are omitted, never rendered empty."""
    spec = spec or {}
    out: list[str] = [f"# {title or 'Untitled idea'}", '']

    if spec.get('summary'):
        out += [spec['summary'].strip(), '']

    if spec.get('goals'):
        out += ['## Goals', ''] + _bullets(spec['goals']) + ['']
    if spec.get('nonGoals'):
        out += ['## Non-goals', ''] + _bullets(spec['nonGoals']) + ['']

    # Deterministic: straight from the assessment's cited evidence.
    if evidence:
        out += ['## What already exists', '']
        for item in evidence:
            location = item.get('file') or ''
            if item.get('line'):
                location += f":{item['line']}"
            out.append(f"- **{item.get('ref')}** ({item.get('kind')}) — `{location}`")
        out.append('')

    # Deterministic: the user's own answers.
    if answered:
        out += ['## Decisions already made', '']
        out += [f"- **{a['question']}** → {a['answer']}" for a in answered]
        out.append('')

    if spec.get('dataModel'):
        out += ['## Data model', '']
        for table in spec['dataModel']:
            out.append(f"- **`{table['table']}`** — {table.get('purpose', '')}")
            for column in table.get('columns') or []:
                out.append(f"  - `{column}`")
        out.append('')

    if spec.get('api'):
        out += ['## API', '']
        out += [
            f"- `{e.get('method', '').upper()} {e.get('path', '')}` — {e.get('purpose', '')}"
            for e in spec['api']
        ]
        out.append('')

    if spec.get('frontend'):
        out += ['## Frontend', '']
        out += [f"- `{f.get('file', '')}` — {f.get('purpose', '')}" for f in spec['frontend']]
        out.append('')

    if spec.get('technicalConsiderations'):
        out += ['## Technical considerations', '']
        out += [f"- **{c['topic']}** — {c['note']}" for c in spec['technicalConsiderations']]
        out.append('')

    if spec.get('phases'):
        out += ['## Suggested phases', '']
        numbered = [(i, _unnumbered(p)) for i, p in enumerate(spec['phases'], start=1)]
        out += [f'{i}. {p}' for i, p in numbered if p]
        out.append('')

    if spec.get('risks'):
        out += ['## Risks', '']
        out += [
            f"- **{r['risk']}**" + (f" — {r['mitigation']}" if r.get('mitigation') else '')
            for r in spec['risks']
        ]
        out.append('')

    if spec.get('testPlan'):
        out += ['## Tests', ''] + _bullets(spec['testPlan']) + ['']

    # Deterministic: still-open questions are a warning on the spec, not prose.
    if open_questions:
        out += ['## Open questions (decide before building)', '']
        out += [f"- {q['question']}" for q in open_questions]
        out.append('')

    if sources:
        out += ['## Sources', '']
        out += [f"- [{s.get('title') or s.get('url')}]({s.get('url')})"
                for s in sources if s.get('url')]
        out.append('')

    return '\n'.join(out).rstrip() + '\n'


def generate_spec(prompt: str) -> dict | None:
    if not is_ai_configured():
        return None
    try:
        return chat_json(prompt, system=SYSTEM_PROMPT, schema=_SCHEMA, max_tokens=MAX_TOKENS)
    except Exception as e:
        logger.warning('Plan generation failed: %s', e)
        return None


def save_plan(idea_id: str, content: str, spec: dict, snapshot_id: str | None,
              now: int | None = None) -> dict:
    """Append a new version. Regenerating never destroys a version you may
    already have handed to a coding agent."""
    db = get_db()
    now = now or int(time.time())
    version = db.execute(
        'SELECT COALESCE(MAX(version), 0) + 1 AS next FROM idea_plans WHERE idea_id=?',
        (idea_id,),
    ).fetchone()['next']
    plan_id = str(ULID())
    db.execute(
        'INSERT INTO idea_plans(id, idea_id, version, content, spec, snapshot_id,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        (plan_id, idea_id, version, content, json.dumps(spec or {}), snapshot_id, now, now),
    )
    db.commit()
    return latest_plan(idea_id)


def latest_plan(idea_id: str) -> dict | None:
    row = get_db().execute(
        'SELECT * FROM idea_plans WHERE idea_id=? ORDER BY version DESC LIMIT 1',
        (idea_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def list_plans(idea_id: str) -> list[dict]:
    rows = get_db().execute(
        'SELECT id, idea_id, version, created_at, updated_at FROM idea_plans'
        ' WHERE idea_id=? ORDER BY version DESC',
        (idea_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]
