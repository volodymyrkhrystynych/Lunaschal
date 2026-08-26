"""One model call that decides whether a posting belongs in the feed, and
condenses it into the snippet the feed actually shows.

The feed used to be a whole job board with a sort applied — the adapters pull
every open posting a company has, and nothing downstream filtered. This is the
layer that filters, and the one that turns a 4,000-word job description into
two sentences you can triage from a phone.

**Judging and condensing are one call, not two.** Both require reading the
whole posting, and that prefill is the expensive part; asking for the verdict
and the summary together costs one pass over the description instead of two.

**What can be computed is computed before this runs.** `backend/jobs/triage.py`
already extracted the stated seniority, the years of experience demanded, and
whether those two disagree; `keywords.py` already worked out which of the
posting's requirements the profile can evidence. All of it is handed over as
fact. The model is asked only for what actually needs judgement — is this the
kind of work they do, and what does the posting look like once it is boiled
down — which is the same trade `keywords.py` and `repo_facts.py` make.

**The model cannot invent a requirement.** `missingMustHaves` is an `enum`
bound to the terms `keyword_report` already returned as missing, so llama-server
compiles the bound into the grammar and an unlisted term cannot be decoded at
all — the `tailor.py` trick, for the same reason: this text is trusted enough
to skip a posting on.

How this differs from `job_match.py`, which stays: that one runs when you open
a posting you are already interested in, and answers "what should this
application lead with". This one runs over everything, before you have looked
at any of it, and answers "should this be on the screen".
"""
from __future__ import annotations

import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_TOKENS = 500

# A job description past this is boilerplate — benefits, EEO statements, the
# company's founding story. The requirements are always near the top, and the
# tail costs prefill on every single posting.
MAX_DESCRIPTION_CHARS = 6000

# What the model may raise, beyond the mismatch `triage.py` computes exactly.
# A closed vocabulary rather than free text so the UI can render each one as a
# known pill and the set stays greppable.
FLAG_KINDS = (
    'seniority_mismatch',
    'unpaid',
    'commission_only',
    'unclear_role',
    'contract_only',
    'onsite_required',
    'security_clearance',
    'heavy_travel',
    'stack_mismatch',
)

FIT_LEVELS = ('strong', 'possible', 'stretch')

SYSTEM = """You triage job postings for one software engineer, before they \
have looked at any of them. Two jobs, in one answer.

FIRST: is this worth their screen at all? Set relevant=false for anything that \
is not software, data, ML/AI, infrastructure or closely adjacent technical \
work. A marketing, sales, finance, HR, clerical, trades, healthcare or \
hospitality posting is relevant=false even when it mentions software tools. \
Be decisive — this filter is the point. But a technical role in an unfamiliar \
domain is still technical: judge the work, not the industry. Whenever you set \
relevant=false, `reason` must say in one short clause what the job actually is \
("marketing ops, not engineering"); that line is all the user sees when \
auditing what was filtered out.

SECOND: if it is relevant, condense it. `summary` is at most two sentences \
saying what the job actually is — the work, the stack, the shape of the team. \
Not the company's self-description, not adjectives. Assume they will decide \
from this alone and never open the posting.

`flags` is for what a careful reader would notice and resent finding out \
later: a title that disagrees with the experience demanded, unpaid or \
commission-only work, a role so vaguely described it could be anything, \
required clearance, mandatory relocation. Do not flag ordinary things. \
An empty list is the common and correct answer.

`fit` compares the posting to their background as given:
- 'strong'  — they could apply today and be a credible candidate
- 'possible'— real gaps, still worth an application
- 'stretch' — a hard requirement is missing

Facts you are given have already been computed. Treat them as true and do not \
re-derive or dispute them."""

_BASE_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'relevant': {'type': 'boolean'},
        'reason': {'type': 'string'},
        'fit': {'type': 'string', 'enum': list(FIT_LEVELS)},
        'summary': {'type': 'string'},
        'flags': {
            'type': 'array',
            'maxItems': 4,
            'items': {
                'type': 'object',
                'properties': {
                    'kind': {'type': 'string', 'enum': list(FLAG_KINDS)},
                    'detail': {'type': 'string'},
                },
                'required': ['kind', 'detail'],
                'additionalProperties': False,
            },
        },
    },
    # `reason` is required so a rejection can always explain itself: the
    # filtered list exists to be audited, and a row that says only 'no' is
    # not auditable.
    'required': ['relevant', 'reason', 'fit', 'summary', 'flags'],
    'additionalProperties': False,
}


def build_schema(missing_terms: list[str] | None) -> dict:
    """The response schema, with `missingMustHaves` bound to real terms.

    The enum is what makes the field trustworthy: llama-server compiles it to a
    grammar, so a requirement the posting never stated cannot be produced. With
    no terms to bind — an empty profile, or a posting mentioning nothing in the
    vocabulary — the field is omitted entirely rather than left unbounded,
    because an unbounded list here is exactly the invented-experience failure
    the bound exists to prevent.
    """
    schema = {
        **_BASE_SCHEMA,
        'properties': dict(_BASE_SCHEMA['properties']),
    }
    terms = [t for t in (missing_terms or []) if t]
    if terms:
        schema['properties']['missingMustHaves'] = {
            'type': 'array',
            'maxItems': 6,
            'items': {'type': 'string', 'enum': terms},
        }
    return schema


def _facts_block(facts: dict, report: dict) -> str:
    lines = []
    if facts.get('seniority'):
        lines.append(f"Title states seniority: {facts['seniority']}")
    if facts.get('yearsRequired') is not None:
        lines.append(f"Years of experience demanded: {facts['yearsRequired']}")
    if facts.get('seniorityMismatch'):
        lines.append(
            'MISMATCH: the title is junior/intern but the body demands '
            f"{facts.get('yearsRequired')} years. Flag this as "
            'seniority_mismatch.'
        )
    matched = ', '.join(report.get('matched') or [])
    missing = ', '.join(report.get('missing') or [])
    if matched:
        lines.append(f'Requirements they can evidence: {matched}')
    if missing:
        lines.append(f'Requirements they cannot evidence: {missing}')
    if report.get('partial'):
        lines.append(
            'NOTE: only a truncated snippet of this posting was available, so '
            'the keyword analysis understates it.'
        )
    return '\n'.join(lines) or 'None computed.'


def triage_posting(
    job: dict,
    profile_summary: str,
    facts: dict,
    report: dict,
) -> dict | None:
    """Judge and condense one posting. None when the model is unavailable.

    None rather than a default verdict, for the reason the whole cascade is
    built this way: a 'relevant' that was never actually decided is
    indistinguishable from one that was, and the caller needs to leave the row
    pending and try again rather than record a guess.
    """
    if not is_ai_configured():
        return None

    description = (job.get('description') or '')[:MAX_DESCRIPTION_CHARS]
    prompt = f"""POSTING
Title: {job.get('title') or ''}
Company: {job.get('company') or ''}
Location: {job.get('location') or ''}

{description}

THEIR BACKGROUND
{profile_summary or 'Not yet provided.'}

ALREADY COMPUTED — treat as fact
{_facts_block(facts or {}, report or {})}"""

    schema = build_schema((report or {}).get('missing'))
    try:
        result = chat_json(
            prompt, system=SYSTEM, schema=schema, max_tokens=MAX_TOKENS
        )
    except Exception as e:
        logger.warning('Job triage failed for %s: %s', job.get('id'), e)
        return None

    if not isinstance(result, dict) or 'relevant' not in result:
        return None
    return normalize_result(result, facts or {})


def normalize_result(result: dict, facts: dict) -> dict:
    """Clamp the model's answer back inside its bounds.

    The grammar already enforces these, so this is belt-and-braces in the shape
    `tailor.clamp` uses — and it is what makes the module safe to call with a
    schema-less fallback provider.

    It also *adds* the computed mismatch flag when the model failed to raise
    it. That flag is a regex result, not an opinion, and the posting is exactly
    the kind where a careful reader would want it.
    """
    flags = []
    for flag in result.get('flags') or []:
        if not isinstance(flag, dict):
            continue
        kind = flag.get('kind')
        if kind in FLAG_KINDS:
            flags.append({'kind': kind, 'detail': str(flag.get('detail') or '')})

    if facts.get('seniorityMismatch') and not any(
        f['kind'] == 'seniority_mismatch' for f in flags
    ):
        years = facts.get('yearsRequired')
        flags.insert(0, {
            'kind': 'seniority_mismatch',
            'detail': f'Titled {facts.get("seniority")}, but asks for {years} years.',
        })

    fit = result.get('fit')
    return {
        'relevant': bool(result.get('relevant')),
        'reason': str(result.get('reason') or '')[:300],
        'fit': fit if fit in FIT_LEVELS else 'possible',
        'summary': str(result.get('summary') or '').strip(),
        'flags': flags[:5],
        'missingMustHaves': [
            t for t in (result.get('missingMustHaves') or []) if isinstance(t, str)
        ][:6],
    }
