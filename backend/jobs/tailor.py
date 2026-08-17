"""Tailoring a resume to one posting, without letting the model invent a career.

Two mechanisms do the work, and neither is a prompt instruction:

1. **The model selects bullets by index, and the schema bounds the index to the
   real list.** llama-server compiles the JSON Schema to a GBNF grammar, so
   `{"index": 47}` against a 12-bullet profile is not rejected after the fact —
   it cannot be decoded. Same trick as `backend/ai/idea_assessment.py`.

2. **The keyword list is computed, not generated.** `backend/jobs/keywords.py`
   works out which of the posting's terms the profile can actually evidence,
   and only those are offered to the model — as a schema enum, so the emphasis
   list cannot name a skill the user does not have. The missing terms are shown
   too, explicitly labelled as forbidden.

What neither mechanism can prevent is inflation *inside* a rewrite: "helped
with" becoming "led". So the original text is kept alongside every rewrite in
the stored result, and the UI shows both. The last check on a resume is the
person whose name is on it.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.jobs import keywords as kw

logger = logging.getLogger(__name__)

MAX_TOKENS = 3072
MAX_JD_CHARS = 8000
MAX_STEER_CHARS = 2000
MAX_SUMMARY_WORDS = 60

SYSTEM_PROMPT = """You tailor an existing resume to one job posting. You are \
an editor, never an author.

You are given a numbered list of the candidate's real accomplishments, the \
posting, and a keyword analysis.

Rules:
- Select accomplishments by their number. You cannot cite a number that is not \
in the list, so if the profile is thin, select fewer.
- Rewriting means re-angling the same fact toward this posting: reordering it, \
leading with the part that matters here, or using the posting's vocabulary for \
something the candidate already did. It never means adding scope, headcount, \
metrics, technologies or outcomes that are not in the original wording.
- If an accomplishment already reads well for this posting, return it unchanged.
- Mirror the posting's exact terminology for the SUPPORTED keywords — those are \
the ones the profile can back up, and matching their wording is what gets past \
resume screeners.
- Never use a MISSING keyword. Those are the things the posting wants and the \
candidate has not evidenced. Writing one in is a lie that a first interview \
will expose.
- The summary is at most 60 words, written in the first person without "I", and \
must only restate things visible in the selected accomplishments.
- Order selections by relevance to this posting, most relevant first."""


def build_schema(bullet_count: int, supported_keywords: list[str]) -> dict:
    """The tailoring schema with both bounds applied.

    `index` is clamped to the real bullet list and `emphasis` to the keywords
    the profile can support, so the two things worth lying about are both
    unrepresentable in the grammar.
    """
    schema = {
        'type': 'object',
        'properties': {
            'summary': {'type': 'string'},
            'selectedBullets': {
                'type': 'array',
                'maxItems': max(bullet_count, 1),
                'items': {
                    'type': 'object',
                    'properties': {
                        'index': {'type': 'integer', 'minimum': 0,
                                  'maximum': max(bullet_count - 1, 0)},
                        'rewritten': {'type': 'string'},
                    },
                    'required': ['index', 'rewritten'],
                },
            },
        },
        'required': ['summary', 'selectedBullets'],
    }

    if bullet_count == 0:
        # Nothing to select: an empty array is the only valid answer.
        schema['properties']['selectedBullets'] = {
            'type': 'array', 'maxItems': 0, 'items': {'type': 'object'},
        }

    if supported_keywords:
        schema['properties']['emphasis'] = {
            'type': 'array',
            'maxItems': min(len(supported_keywords), 12),
            'items': {'type': 'string', 'enum': list(supported_keywords)},
        }

    return schema


def build_prompt(
    bullets: list[dict],
    job: dict,
    report: kw.KeywordReport,
    steer: str = '',
) -> str:
    parts = []

    header = f"# The posting\n\n{job.get('title') or '(untitled)'}"
    if job.get('company'):
        header += f" at {job['company']}"
    if job.get('location'):
        header += f" ({job['location']})"
    parts.append(header + '\n\n' + (job.get('description') or '')[:MAX_JD_CHARS])

    if bullets:
        listing = '\n'.join(
            f"{b['index']}. [{b['company']} — {b['title']}] {b['text']}" for b in bullets
        )
    else:
        listing = '(the profile has no accomplishments recorded)'
    parts.append('# The candidate\'s real accomplishments\n\n' + listing)

    analysis = []
    if report.matched:
        analysis.append(
            'SUPPORTED (the profile evidences these — mirror the posting\'s '
            'exact wording):\n' + ', '.join(report.matched)
        )
    if report.missing:
        analysis.append(
            'MISSING (the posting asks for these and the profile does NOT '
            'evidence them — never claim any of them):\n' + ', '.join(report.missing)
        )
    if analysis:
        parts.append('# Keyword analysis\n\n' + '\n\n'.join(analysis))

    if steer:
        # Last, and labelled as the user's own words, so it outranks the
        # generic guidance above without being able to unset the rules.
        parts.append(
            '# What the candidate asked for\n\n'
            + steer[:MAX_STEER_CHARS]
            + '\n\nFollow this, except where it would require claiming '
              'something the accomplishments above do not support.'
        )

    return '\n\n'.join(parts)


def clamp(result: dict, bullets: list[dict], report: kw.KeywordReport) -> dict:
    """Resolve the model's answer against the real data.

    The grammar should make every bound here redundant. They are applied
    anyway, because "should" is doing a lot of work in that sentence and the
    output of this function ends up on a document with the user's name on it.
    """
    by_index = {b['index']: b for b in bullets}
    supported = set(report.matched)

    selected = []
    seen = set()
    for item in (result.get('selectedBullets') or []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get('index'))
        except (TypeError, ValueError):
            continue
        bullet = by_index.get(index)
        if bullet is None or index in seen:
            continue
        seen.add(index)
        rewritten = (item.get('rewritten') or '').strip()
        selected.append({
            'bulletId': bullet['id'],
            # Kept so the renderer can group bullets back under their role
            # without re-deriving it from the company name.
            'roleId': bullet['roleId'],
            'index': index,
            'company': bullet['company'],
            'roleTitle': bullet['title'],
            # Both kept: the UI shows the change so the user approves the
            # wording rather than discovering it in an interview.
            'original': bullet['text'],
            'text': rewritten or bullet['text'],
            'rewritten': bool(rewritten and rewritten != bullet['text']),
        })

    summary = ' '.join((result.get('summary') or '').split())
    words = summary.split()
    if len(words) > MAX_SUMMARY_WORDS:
        summary = ' '.join(words[:MAX_SUMMARY_WORDS]).rstrip(',;:') + '…'

    emphasis = [k for k in (result.get('emphasis') or []) if k in supported]

    return {
        'summary': summary,
        'selectedBullets': selected,
        'emphasis': emphasis,
        'keywords': report.to_dict(),
    }


MAX_BULLET_CHARS = 600


def apply_edits(content: dict, patch: dict) -> dict:
    """Merge a user's corrections into a stored tailoring result.

    **The user's wording is not clamped against the profile.** `clamp` above
    exists to stop *the model* inventing experience; this is the person whose
    name is on the document, and re-applying that bound here would silently
    delete their own edit. What is protected instead is *structure*: the patch
    can reword, reorder and remove bullets, but `bulletId`, `roleId`,
    `company`, `roleTitle` and `original` come from the stored row, so an edit
    cannot re-attribute an accomplishment to a company the user never worked
    at, and the diff the UI shows keeps a truthful "before".

    A bullet absent from `patch['bullets']` is dropped — that is how one is
    removed — and the list order becomes the new order.
    """
    stored = {
        b['bulletId']: b
        for b in (content.get('selectedBullets') or [])
        if isinstance(b, dict) and b.get('bulletId')
    }

    bullets = []
    seen = set()
    for item in (patch.get('bullets') or []):
        if not isinstance(item, dict):
            continue
        bullet_id = item.get('bulletId')
        base = stored.get(bullet_id)
        if base is None or bullet_id in seen:
            continue
        seen.add(bullet_id)

        text = ' '.join(str(item.get('text') or '').split())[:MAX_BULLET_CHARS]
        if not text:
            text = base['original']
        merged = dict(base)
        merged['text'] = text
        merged['rewritten'] = text != base.get('original')
        bullets.append(merged)

    result = dict(content)
    result['selectedBullets'] = bullets

    if 'summary' in patch:
        words = ' '.join(str(patch.get('summary') or '').split()).split()
        if len(words) > MAX_SUMMARY_WORDS:
            result['summary'] = ' '.join(words[:MAX_SUMMARY_WORDS]).rstrip(',;:') + '…'
        else:
            result['summary'] = ' '.join(words)

    return result


def tailor_resume(loaded: dict, job: dict, steer: str = '') -> dict | None:
    """Tailor the loaded profile to `job`. None when the model is unavailable.

    None rather than a fallback, for the reason `polish_journal_entry` raises:
    a generic resume that silently pretends to be a tailored one is worse than
    an honest failure, because you cannot tell them apart after the fact.
    """
    from backend.jobs import profile as profile_mod

    bullets = profile_mod.flat_bullets(loaded)
    report = kw.keyword_report(
        job.get('description') or '',
        profile_mod.profile_text(loaded),
        profile_mod.skill_names(loaded),
    )

    if not is_ai_configured():
        return None

    try:
        raw = chat_json(
            build_prompt(bullets, job, report, steer),
            system=SYSTEM_PROMPT,
            schema=build_schema(len(bullets), report.matched),
            max_tokens=MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Resume tailoring failed: %s', e)
        return None

    return clamp(raw, bullets, report)
