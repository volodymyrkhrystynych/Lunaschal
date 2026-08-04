"""The one LLM call in the repo-context pipeline: summarizing the delta.

Everything else about the repo is extracted exactly
(backend/research/repo_facts.py). The model is only asked what changed since
the last snapshot, because that is the one question a `git log` alone answers
badly — a list of commit subjects is not a description of what the app can now
do.

Returns None on any failure. The caller stores the snapshot regardless: the
deterministic facts are the product, and the prose is a convenience.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

# The delta is a handful of commit subjects and a diffstat, so this is a small
# call — thinking stays off and the ceiling is low.
MAX_TOKENS = 1024
MAX_INPUT_CHARS = 8000

SYSTEM_PROMPT = """You summarize what changed in a personal life-management app \
between two snapshots of its git history.

You are given commit subjects and a diffstat. Write for a reader who knows the \
app but has not seen this week's work, and who will use your summary to decide \
whether a feature idea is already built.

Rules:
- Describe capabilities, not file churn. "Ideas can now borrow a Paper page as \
a sketch" beats "modified 6 files in src/components/Ideas".
- Only claim what the commits and diffstat actually support. Do not guess at \
motivation or at work that is not shown.
- If the delta is trivial or empty, say so in the headline and return no changes."""

_SCHEMA = {
    'type': 'object',
    'properties': {
        'headline': {'type': 'string'},
        'changes': {
            'type': 'array',
            'maxItems': 8,
            'items': {
                'type': 'object',
                'properties': {
                    'area': {'type': 'string'},
                    'summary': {'type': 'string'},
                },
                'required': ['area', 'summary'],
            },
        },
    },
    'required': ['headline', 'changes'],
}


def summarize_delta(commits: list[str], diffstat: str) -> dict | None:
    """{headline, changes:[{area, summary}]} or None when unavailable."""
    if not is_ai_configured() or not commits:
        return None

    body = 'Commits:\n' + '\n'.join(f'- {c}' for c in commits)
    if diffstat:
        body += f'\n\nDiffstat:\n{diffstat}'
    body = body[:MAX_INPUT_CHARS]

    try:
        result = chat_json(
            body, system=SYSTEM_PROMPT, schema=_SCHEMA, max_tokens=MAX_TOKENS
        )
    except Exception as e:
        logger.warning('Repo-context delta summary failed: %s', e)
        return None

    headline = (result.get('headline') or '').strip()
    if not headline:
        return None
    changes = [
        {'area': c.get('area', '').strip(), 'summary': c.get('summary', '').strip()}
        for c in result.get('changes') or []
        if isinstance(c, dict) and c.get('summary')
    ]
    return {'headline': headline, 'changes': changes}


def render_change_summary(summary: dict | None) -> str | None:
    """Markdown for the snapshot's change_summary column. Pure."""
    if not summary:
        return None
    lines = [summary['headline'].strip()]
    if summary.get('changes'):
        lines.append('')
        lines += [f"- **{c['area']}** — {c['summary']}" for c in summary['changes'] if c['area']]
        lines += [f"- {c['summary']}" for c in summary['changes'] if not c['area']]
    return '\n'.join(lines)
