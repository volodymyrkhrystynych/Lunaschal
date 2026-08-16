"""Answering an application form's questions.

Three sources, tried in order, and the model is the last one:

1. **The profile** — name, email, phone, location, links. These are facts, and
   asking a language model to reproduce a phone number is a way to get a phone
   number that is nearly right.
2. **The answer bank** — the standard questions every portal asks (work
   authorization, notice period, salary). Answered verbatim from what the user
   wrote once. This is what makes the second tap instant and free.
3. **The model**, for the rest, steered by whatever the user dictated.

The model call is shaped as *one object property per question* rather than an
array of answers. That lets every question carry its own schema — a dropdown
becomes an enum of its real options, a numeric field becomes an integer — so
llama-server's grammar makes an unselectable answer undecodable rather than
merely unlikely. An array could not express per-item constraints.
"""
import logging
import re

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
MAX_QUESTIONS = 40
MAX_JD_CHARS = 4000
MAX_STEER_CHARS = 2000

QUESTION_TYPES = ('text', 'textarea', 'select', 'boolean', 'number')

SYSTEM_PROMPT = """You fill in a job application form as the candidate, in the \
first person.

You are given the candidate's profile, the posting, and the questions still \
unanswered. Answer every one.

Rules:
- Only state things the profile supports. If a question asks about experience \
the profile does not show, answer honestly about what is there instead of \
inventing it.
- Match the length to the field: a short-answer box gets one or two sentences, \
a long-answer box gets a short paragraph. Never pad.
- Write plainly and specifically. Name the actual work. Avoid "passionate", \
"leverage", "synergy", and anything else that would survive being pasted into \
a different application unchanged.
- Do not open with "As a" or "I am writing to". Start with the substance."""

# Profile fields that answer themselves, matched on what the label contains.
# Ordered: the first pattern that matches wins, so 'email' is tested before the
# broader 'name' catches "email name".
_PROFILE_PATTERNS: list[tuple[str, str]] = [
    (r'\b(e-?mail)\b', 'email'),
    (r'\b(phone|mobile|telephone|cell)\b', 'phone'),
    (r'\b(full name|your name|legal name|candidate name)\b', 'full_name'),
    (r'\b(first name|given name)\b', 'first_name'),
    (r'\b(last name|surname|family name)\b', 'last_name'),
    (r'\b(city|location|where are you based|current location)\b', 'location'),
    (r'\blinked-?in\b', 'linkedin'),
    (r'\bgit-?hub\b', 'github'),
    (r'\b(portfolio|personal (web)?site|website|homepage)\b', 'website'),
]

_LINK_SLUGS = {'linkedin', 'github', 'website'}


def _normalize(text: str) -> str:
    return ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', (text or '').lower()).split())


def _link_for(profile: dict, slug: str) -> str:
    """Find a profile link by its label, or by the host for the two named sites."""
    for link in profile.get('links') or []:
        if not isinstance(link, dict):
            continue
        label = _normalize(link.get('label', ''))
        url = (link.get('url') or '').lower()
        if slug == 'website':
            if label in ('website', 'portfolio', 'site', 'homepage'):
                return link.get('url', '')
        elif slug in label or slug in url:
            return link.get('url', '')
    if slug == 'website':
        # Fall back to any link that isn't one of the two named profiles.
        for link in profile.get('links') or []:
            url = (link.get('url') or '').lower()
            if isinstance(link, dict) and url and not any(s in url for s in ('linkedin', 'github')):
                return link.get('url', '')
    return ''


def profile_answer(question: str, profile: dict) -> str | None:
    """A direct answer from the contact block, or None."""
    label = _normalize(question)
    if not label:
        return None
    for pattern, slug in _PROFILE_PATTERNS:
        if not re.search(pattern, label):
            continue
        if slug in _LINK_SLUGS:
            return _link_for(profile, slug) or None
        if slug == 'first_name':
            return (profile.get('full_name') or profile.get('fullName') or '').split(' ')[0] or None
        if slug == 'last_name':
            parts = (profile.get('full_name') or profile.get('fullName') or '').split(' ')
            return parts[-1] if len(parts) > 1 else None
        value = profile.get(slug) or profile.get(_camel(slug))
        return value or None
    return None


def _camel(snake: str) -> str:
    head, *rest = snake.split('_')
    return head + ''.join(w.capitalize() for w in rest)


def _similarity(a: str, b: str) -> float:
    """Token overlap over the shorter side.

    Deliberately not a string distance: "Are you legally authorized to work in
    Canada?" and "Work authorization" share few characters and most of their
    meaningful words.
    """
    ta, tb = set(_normalize(a).split()), set(_normalize(b).split())
    ta.discard('the'); ta.discard('you'); ta.discard('your'); ta.discard('are')
    tb.discard('the'); tb.discard('you'); tb.discard('your'); tb.discard('are')
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


BANK_MATCH_THRESHOLD = 0.7


def bank_answer(question: str, bank: list[dict]) -> str | None:
    """The saved answer whose question means the same thing, or None."""
    best, best_score = None, 0.0
    for entry in bank:
        if not entry.get('answer'):
            continue
        score = _similarity(question, entry.get('question', ''))
        if score > best_score:
            best, best_score = entry, score
    return best['answer'] if best and best_score >= BANK_MATCH_THRESHOLD else None


def _question_schema(question: dict) -> dict:
    kind = question.get('type') or 'text'
    options = [o for o in (question.get('options') or []) if isinstance(o, str) and o]
    if kind == 'select' and options:
        return {'type': 'string', 'enum': options}
    if kind == 'boolean':
        return {'type': 'string', 'enum': options or ['Yes', 'No']}
    if kind == 'number':
        return {'type': 'integer'}
    return {'type': 'string'}


def build_schema(questions: list[dict]) -> dict:
    """One bounded property per unanswered question, all required."""
    properties = {f'q{i}': _question_schema(q) for i, q in enumerate(questions)}
    return {
        'type': 'object',
        'properties': properties,
        'required': list(properties),
    }


def build_prompt(
    questions: list[dict], loaded: dict, job: dict, steer: str = ''
) -> str:
    from backend.jobs import profile as profile_mod

    parts = [f'# About the candidate\n\n{profile_mod.profile_text(loaded)}']

    header = f"# The posting\n\n{job.get('title') or '(untitled)'}"
    if job.get('company'):
        header += f" at {job['company']}"
    parts.append(header + '\n\n' + (job.get('description') or '')[:MAX_JD_CHARS])

    lines = []
    for i, q in enumerate(questions):
        line = f"q{i}. {q.get('label') or '(unlabelled field)'}"
        if q.get('options'):
            line += f"  [choose exactly one of: {', '.join(q['options'])}]"
        elif q.get('type') == 'textarea':
            line += '  [long answer]'
        elif q.get('type') == 'number':
            line += '  [a whole number]'
        lines.append(line)
    parts.append('# Questions to answer\n\n' + '\n'.join(lines))

    if steer:
        parts.append(
            '# What the candidate asked for\n\n'
            + steer[:MAX_STEER_CHARS]
            + '\n\nFollow this, except where it would require claiming '
              'something the profile does not support.'
        )

    return '\n\n'.join(parts)


def answer_questions(
    questions: list[dict], loaded: dict, job: dict, steer: str = ''
) -> list[dict]:
    """Answer every question, tagging each with where the answer came from.

    Never raises and never returns a short list: a question the model could not
    answer comes back with an empty answer and source 'unanswered', because a
    silently dropped field is one the user pastes a blank into.
    """
    questions = questions[:MAX_QUESTIONS]
    profile = loaded.get('profile', {})
    bank = loaded.get('answers', [])

    resolved: list[dict | None] = []
    pending: list[tuple[int, dict]] = []

    for i, q in enumerate(questions):
        label = q.get('label') or ''
        direct = profile_answer(label, profile)
        if direct:
            resolved.append({'answer': direct, 'source': 'profile'})
            continue
        saved = bank_answer(label, bank)
        if saved:
            resolved.append({'answer': saved, 'source': 'bank'})
            continue
        resolved.append(None)
        pending.append((i, q))

    if pending and is_ai_configured():
        pending_questions = [q for _, q in pending]
        try:
            raw = chat_json(
                build_prompt(pending_questions, loaded, job, steer),
                system=SYSTEM_PROMPT,
                schema=build_schema(pending_questions),
                max_tokens=MAX_TOKENS,
            )
        except Exception as e:
            logger.warning('Answer generation failed: %s', e)
            raw = {}
        for slot, (original_index, _) in enumerate(pending):
            value = raw.get(f'q{slot}')
            if value is None or value == '':
                continue
            resolved[original_index] = {'answer': str(value), 'source': 'generated'}

    out = []
    for i, q in enumerate(questions):
        entry = resolved[i] or {'answer': '', 'source': 'unanswered'}
        out.append({
            'label': q.get('label') or '',
            'type': q.get('type') or 'text',
            'options': q.get('options') or [],
            'answer': entry['answer'],
            'source': entry['source'],
        })
    return out
