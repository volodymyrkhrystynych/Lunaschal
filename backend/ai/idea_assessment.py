"""Judging an idea against the repo: is it already built, and what's undecided?

Deliberately no web access. This is a question about *our* codebase, and the
answer lives entirely in the repo snapshot — letting the model search would only
add noise it could mistake for evidence.

The schema constrains `evidenceIndexes` to positions in a candidate list we
built (backend/research/evidence.py), so the model selects evidence rather than
writing file paths. Combined with the clamp in backend/research/assess.py, that
is what keeps "already implemented" from becoming a vibe.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
MAX_IDEA_CHARS = 4000
MAX_DIGEST_CHARS = 12000

SYSTEM_PROMPT = """You judge whether a proposed feature already exists in a \
personal life-management app called Lunaschal, and what still needs deciding.

You are given the idea, a numbered list of things in the codebase that might \
already satisfy it, and an inventory of the app.

Rules:
- Cite evidence only by its number in the candidate list. Never write a file \
path or invent a route; if nothing in the list is relevant, cite nothing.
- "implemented" means a user could do this today. Adjacent machinery that would \
have to be extended is "partial", not "yes".
- Being on the roadmap means it was *planned*, which is the opposite of built. \
Never treat a roadmap entry as evidence of implementation.
- openQuestions are decisions only the app's owner can make — genuine forks \
where two reasonable answers lead to different work. Do not pad the list; an \
idea with an obvious shape has none.
- confidence is how sure you are of the verdict, 0 to 1. Be honest and low when \
the evidence is thin."""

SCHEMA_TEMPLATE = {
    'type': 'object',
    'properties': {
        'verdict': {'type': 'string', 'enum': ['no', 'partial', 'yes']},
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'rationale': {'type': 'string'},
        'evidenceIndexes': {'type': 'array', 'maxItems': 8, 'items': {'type': 'integer'}},
        'openQuestions': {
            'type': 'array',
            'maxItems': 5,
            'items': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string'},
                    'why': {'type': 'string'},
                    'options': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string'}},
                },
                'required': ['question'],
            },
        },
        'effort': {'type': 'string', 'enum': ['s', 'm', 'l']},
    },
    'required': ['verdict', 'confidence', 'rationale', 'evidenceIndexes', 'openQuestions'],
}


def build_schema(candidate_count: int) -> dict:
    """The schema with evidence indexes bounded to the candidate list.

    llama-server compiles this to a GBNF grammar, so the bound is enforced
    during decoding rather than checked afterwards.
    """
    schema = {
        'type': 'object',
        'properties': dict(SCHEMA_TEMPLATE['properties']),
        'required': list(SCHEMA_TEMPLATE['required']),
    }
    if candidate_count > 0:
        schema['properties']['evidenceIndexes'] = {
            'type': 'array',
            'maxItems': 8,
            'items': {'type': 'integer', 'minimum': 1, 'maximum': candidate_count},
        }
    else:
        # Nothing to cite: an empty array is the only valid answer.
        schema['properties']['evidenceIndexes'] = {
            'type': 'array', 'maxItems': 0, 'items': {'type': 'integer'},
        }
    return schema


def build_prompt(
    idea: dict,
    candidates_text: str,
    digest: str,
    roadmap: list[str],
    answered: list[dict],
) -> str:
    body = (idea.get('content') or idea.get('rawContent') or '')[:MAX_IDEA_CHARS]
    parts = [
        f"# The idea\n\n{idea.get('title') or '(untitled)'}\n\n{body}",
    ]
    parts.append(
        '# Candidate evidence from the codebase\n\n'
        + (candidates_text or '(nothing in the codebase looks related)')
    )
    if roadmap:
        parts.append(
            '# Already written down in the roadmap (planned, NOT built)\n\n'
            + '\n'.join(f'- {item}' for item in roadmap)
        )
    if answered:
        parts.append(
            '# Decisions the owner has already made\n\n'
            + '\n'.join(f"- {a['question']} → {a['answer']}" for a in answered)
        )
    if digest:
        parts.append(f'# App inventory\n\n{digest[:MAX_DIGEST_CHARS]}')
    return '\n\n'.join(parts)


def assess_idea(
    idea: dict,
    candidates_text: str,
    candidate_count: int,
    digest: str,
    roadmap: list[str] | None = None,
    answered: list[dict] | None = None,
) -> dict | None:
    """Raw model output, or None when unavailable. The caller clamps it."""
    if not is_ai_configured():
        return None
    prompt = build_prompt(idea, candidates_text, digest, roadmap or [], answered or [])
    try:
        return chat_json(
            prompt,
            system=SYSTEM_PROMPT,
            schema=build_schema(candidate_count),
            max_tokens=MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Idea assessment failed: %s', e)
        return None
