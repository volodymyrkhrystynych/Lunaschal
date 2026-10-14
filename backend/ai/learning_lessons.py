"""The two model calls behind importing a coding agent's learnings.

A lesson is a principle with the failure it prevents ("recursive functions
need a depth limit, or a cycle in the data recurses forever"). Both calls
here exist because a card that recites the principle teaches the wrong thing:
"what should a recursive function have?" is answered on reflex and tests
nothing. What a lesson is for is *noticing when it applies*, so the cards
generated from it are scenarios that never name the principle.

`judge_coverage` runs first and decides whether a card already teaches the
lesson. It is shown only the nearest few cards by embedding, so it is cheap,
and its verdict is constrained by grammar to one of those cards or none.
"""
from backend.ai.learning_generation import CARDS_SCHEMA, _parse_cards
from backend.ai.llm import chat_json

LESSON_SYSTEM = """You write spaced-repetition flashcards from one engineering lesson
a developer learned while building software.

The goal is that the developer recognises, in future work, the moment the
lesson applies. So:
1. Write 1-3 cards. Each one is a short, concrete scenario — a situation the
   developer could plausibly be in — that asks what could go wrong or what
   they should do. Never name the principle in the question; the developer
   must recognise it from the situation.
2. Vary the scenarios: different domains or code shapes in which the same
   principle applies, not rewordings of one situation.
3. The answer states what to do, the failure it prevents, and — when the
   lesson says so — when it does not apply. Two or three sentences.
4. Keep everything generic. Never include company names, product names,
   internal service names, file paths or code that appears in the lesson;
   abstract them into ordinary examples.
5. If the lesson is too vague to test, return fewer cards rather than padding.

Respond with valid JSON: {"cards": [{"question": "...", "answer": "..."}, ...]}"""

COVERAGE_SYSTEM = """You decide whether a developer's flashcard deck already teaches
a new engineering lesson.

You are given the lesson and a numbered list of existing cards. A card covers
the lesson when reviewing that card would already train the developer to
apply the same principle — even if its wording or example differs. A card on
a related but different principle (say, a timeout where the lesson is a
recursion depth limit) does not cover it.

Answer with the number of the covering card, or 0 when none covers it, and
one short sentence saying why."""


def _coverage_schema(n: int) -> dict:
    return {
        'type': 'object',
        'properties': {
            'card': {'type': 'integer', 'enum': list(range(n + 1))},
            'reason': {'type': 'string'},
        },
        'required': ['card', 'reason'],
    }


def _lesson_text(title: str, body: str) -> str:
    return f'{title}\n\n{body}'.strip()


def judge_coverage(title: str, body: str, candidates: list[dict]) -> tuple[int | None, str]:
    """Index into `candidates` of the card that already covers the lesson,
    or None, with the model's one-line reason."""
    if not candidates:
        return None, ''
    listing = '\n\n'.join(
        f'{i}. Q: {c["question"]}\n   A: {c["answer"]}'
        for i, c in enumerate(candidates, start=1)
    )
    prompt = f'Lesson:\n{_lesson_text(title, body)}\n\nExisting cards:\n\n{listing}'
    result = chat_json(prompt, system=COVERAGE_SYSTEM,
                       schema=_coverage_schema(len(candidates)))
    try:
        index = int(result.get('card') or 0)
    except (TypeError, ValueError):
        index = 0
    reason = str(result.get('reason') or '').strip()
    if 1 <= index <= len(candidates):
        return index - 1, reason
    return None, reason


def generate_lesson_cards(title: str, body: str) -> list[dict]:
    prompt = f'Lesson:\n\n{_lesson_text(title, body)}'
    return _parse_cards(chat_json(prompt, system=LESSON_SYSTEM, schema=CARDS_SCHEMA))
