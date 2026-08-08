"""Claim-coverage grading: decompose the stored answer, check what the user's
answer covered, and map coverage to a suggested Again/Hard/Good/Easy rating.

Correctness (did the answer cover the claims) is the LLM's job here; recall
difficulty is the user's — the suggested rating only pre-selects a button.
"""
from backend.ai.llm import chat_json

# Cheap-gate threshold: below this cosine similarity the answer doesn't even
# resemble the stored one, so the full claim-check LLM call is skipped and the
# suggestion is Again. High similarity never skips the LLM — embeddings can't
# see negation or wrong-number errors, and a false "you got it" is the costly
# failure mode.
GATE_LOW = 0.35

CLAIMS_SYSTEM = """You decompose a flashcard answer into its key claims for grading.

Given a question and its ground-truth answer, list the distinct factual claims
the answer makes. Mark each claim "essential": true if the answer is wrong or
incomplete without it, false if it is supporting nuance or extra detail.
Keep claims short — one fact each. Most concise answers have 1-3 claims.

Respond with valid JSON: {"claims": [{"text": "...", "essential": true}, ...]}"""

COVERAGE_SYSTEM = """You grade whether a user's answer to a flashcard covers the key claims
of the ground-truth answer.

For each claim, decide if the user's answer expresses it (different wording is
fine; contradicting or omitting it is not covered). Add a short note only where
it helps ("said 4 instead of 8"). Then write a one-sentence summary of what was
got and what was missed, addressed to the user.

Respond with valid JSON:
{"claims": [{"text": "...", "essential": true, "covered": true, "note": ""}, ...],
 "summary": "..."}"""

# Appended to COVERAGE_SYSTEM only when the caller requests speech feedback —
# a distinct, spoken-friendly field, not a rephrase of "summary" (which is
# rendered as text and is allowed to be denser/technical).
SPEECH_ADDENDUM = """

Also include "speechSummary": one to two short sentences, addressed to the
user, that will be read aloud by text-to-speech. Name the one or two main
things they got wrong (or a brief "you got it" if nothing essential was
missed). Plain spoken language only — no code, no symbols, no markdown, no
quoted literals, nothing that only makes sense written down.

Respond with valid JSON:
{"claims": [{"text": "...", "essential": true, "covered": true, "note": ""}, ...],
 "summary": "...", "speechSummary": "..."}"""


CLAIMS_SCHEMA = {
    'type': 'object',
    'properties': {
        'claims': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {'text': {'type': 'string'},
                               'essential': {'type': 'boolean'}},
                'required': ['text', 'essential'],
            },
        },
    },
    'required': ['claims'],
}

# Same claim list plus the grading verdict. `covered` being a real boolean matters:
# check_coverage feeds it straight into the suggested rating, and a model that
# answered "partially" in a string field used to read as falsy.
COVERAGE_SCHEMA = {
    'type': 'object',
    'properties': {
        'claims': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {'text': {'type': 'string'},
                               'essential': {'type': 'boolean'},
                               'covered': {'type': 'boolean'},
                               'note': {'type': 'string'}},
                'required': ['text', 'covered'],
            },
        },
        'summary': {'type': 'string'},
    },
    'required': ['claims', 'summary'],
}

# Same as COVERAGE_SCHEMA plus the spoken blurb, used only when speech
# feedback was requested — kept separate so a normal grade never pays for it.
COVERAGE_SCHEMA_SPEECH = {
    **COVERAGE_SCHEMA,
    'properties': {**COVERAGE_SCHEMA['properties'], 'speechSummary': {'type': 'string'}},
    'required': [*COVERAGE_SCHEMA['required'], 'speechSummary'],
}


def decompose_claims(question: str, answer: str) -> list[dict]:
    result = chat_json(f'Question: {question}\n\nGround-truth answer: {answer}',
                       system=CLAIMS_SYSTEM, schema=CLAIMS_SCHEMA)
    claims = []
    for c in result.get('claims') or []:
        if isinstance(c, dict) and c.get('text'):
            claims.append({'text': str(c['text']), 'essential': bool(c.get('essential', True))})
    # A grading pipeline with zero claims can't produce feedback; fall back to
    # treating the whole answer as one essential claim.
    return claims or [{'text': answer, 'essential': True}]


def check_coverage(claims: list[dict], user_answer: str, speech: bool = False) -> dict:
    claims_text = '\n'.join(
        f"- {c['text']} (essential: {c['essential']})" for c in claims
    )
    system = COVERAGE_SYSTEM + SPEECH_ADDENDUM if speech else COVERAGE_SYSTEM
    schema = COVERAGE_SCHEMA_SPEECH if speech else COVERAGE_SCHEMA
    result = chat_json(
        f"Ground-truth claims:\n{claims_text}\n\nUser's answer:\n{user_answer}",
        system=system, schema=schema,
    )
    graded = []
    by_text = {c['text']: c for c in claims}
    for c in result.get('claims') or []:
        if not isinstance(c, dict) or not c.get('text'):
            continue
        original = by_text.get(str(c['text']))
        graded.append({
            'text': str(c['text']),
            'essential': original['essential'] if original else bool(c.get('essential', True)),
            'covered': bool(c.get('covered')),
            'note': str(c.get('note') or ''),
        })
    if not graded:  # model dropped the structure; grade conservatively
        graded = [{**c, 'covered': False, 'note': ''} for c in claims]
    out = {'claims': graded, 'summary': str(result.get('summary') or '')}
    if speech:
        out['speechSummary'] = str(result.get('speechSummary') or '')
    return out


def gated_coverage(speech: bool = False) -> dict:
    """Coverage returned when the embedding gate short-circuits the LLM."""
    out = {
        'claims': [],
        'summary': "Your answer doesn't resemble the stored answer.",
        'gated': True,
    }
    if speech:
        out['speechSummary'] = "That didn't match the stored answer — worth a re-read."
    return out


def suggest_rating(coverage: dict) -> int:
    """Map coverage → 1 Again / 2 Hard / 3 Good / 4 Easy."""
    claims = coverage.get('claims') or []
    if coverage.get('gated') or not claims:
        return 1
    essential = [c for c in claims if c['essential']] or claims
    covered_essential = sum(1 for c in essential if c['covered'])
    if covered_essential < len(essential) / 2:
        return 1
    if covered_essential < len(essential):
        return 2
    if any(not c['covered'] for c in claims):
        return 3
    return 4
