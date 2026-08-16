"""The one paragraph explaining whether a posting is worth your time.

Deliberately narrow. The **score is not the model's** — `keywords.py` computes
it deterministically from the posting text against the profile, and the feed is
sorted by that. This call only narrates: it is handed the matched and missing
terms as facts and asked what they add up to.

That split is the whole design. A model scoring two hundred postings is hours
of GPU and a sort order that changes between refreshes; a model writing one
paragraph about the posting you just opened is two seconds and useful. If this
call fails, the feed is unaffected — it loses a sentence, not its ordering.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_TOKENS = 400
MAX_DESCRIPTION_CHARS = 6000

SYSTEM = """You advise one job seeker on whether a posting is worth applying to.

You are given the posting, a summary of their background, and a keyword \
analysis that has ALREADY been computed. Treat that analysis as fact — \
do not re-derive it or dispute it.

Be blunt and specific. "Strong match" tells them nothing; "they want five \
years of Kubernetes and you have none, but the platform work is close" tells \
them what to do. If the gap is fatal, say so — the value here is in filtering \
OUT, and an encouraging assessment of a hopeless posting costs them an \
afternoon.

- verdict: 'strong' only when the missing requirements are genuinely minor.
  'weak' when a hard requirement is absent.
- rationale: two or three sentences, addressed to them, no preamble.
- angle: the single thing their application should lead with, or '' if the
  posting is not worth applying to."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'verdict': {'type': 'string', 'enum': ['strong', 'possible', 'weak']},
        'rationale': {'type': 'string'},
        'angle': {'type': 'string'},
    },
    'required': ['verdict', 'rationale'],
    'additionalProperties': False,
}


def assess_match(job: dict, profile_summary: str, report: dict) -> dict | None:
    """One advisory paragraph about this posting. None when unavailable.

    Returns None rather than a placeholder for the reason the whole module
    exists: a rationale generated without a model would be indistinguishable
    from one that was, and this text is meant to be trusted enough to skip a
    posting on.
    """
    if not is_ai_configured():
        return None

    matched = ', '.join(report.get('matched') or []) or 'none identified'
    missing = ', '.join(report.get('missing') or []) or 'none identified'
    description = (job.get('description') or '')[:MAX_DESCRIPTION_CHARS]

    prompt = f"""POSTING
Title: {job.get('title') or ''}
Company: {job.get('company') or ''}
Location: {job.get('location') or ''}

{description}

THEIR BACKGROUND
{profile_summary}

KEYWORD ANALYSIS (already computed — treat as fact)
Requirements they can evidence: {matched}
Requirements they cannot evidence: {missing}"""

    try:
        result = chat_json(prompt, system=SYSTEM, schema=SCHEMA, max_tokens=MAX_TOKENS)
    except Exception as e:
        logger.warning('Job match assessment failed: %s', e)
        return None

    if not isinstance(result, dict) or not result.get('rationale'):
        return None
    return {
        'verdict': result.get('verdict') or 'possible',
        'rationale': result.get('rationale') or '',
        'angle': result.get('angle') or '',
    }
