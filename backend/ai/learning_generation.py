"""Card generation for the learning feature: brain-dump → atomic cards."""
from backend.ai.llm import chat_json, chat_text

# Atomicity is the highest-leverage part of generation quality: an LLM left
# alone emits compound cards, and the approval queue is a safety net, not the fix.
GENERATE_SYSTEM = """You are a flashcard author for a spaced-repetition system.

Rules — follow all of them:
1. Each card tests exactly ONE atomic concept. Never combine two facts,
   never ask "and how does X relate to Y" in the same card. If a fact is
   compound, split it into multiple cards.
2. Questions are specific and unambiguous out of context.
3. Answers are concise but complete — the shortest statement that fully
   answers the question. No filler, no restating the question.
4. Only card-worthy material: key facts, definitions, mechanisms,
   relationships. Skip meta-commentary, anecdotes, and trivia the source
   itself treats as asides.
5. Generate as many cards as the content genuinely supports — no padding.

Respond with valid JSON: {"cards": [{"question": "...", "answer": "..."}, ...]}"""

REGENERATE_SYSTEM = GENERATE_SYSTEM + """

You are revising a previously generated card the user was not happy with.
Follow the user's direction exactly. If they ask to split the card, return
multiple cards; otherwise return one improved card."""

NORMALIZE_SYSTEM = """You clean up a spoken answer transcript before it is graded.
Remove filler words, false starts, restarts, and self-corrections (keep only the
final corrected version of anything the speaker corrected). Do NOT add, remove,
or reword actual content — keep the substance verbatim. Return only the cleaned
text, nothing else."""


def _parse_cards(result: dict) -> list[dict]:
    cards = result.get('cards') or []
    return [
        {'question': str(c['question']).strip(), 'answer': str(c['answer']).strip()}
        for c in cards
        if isinstance(c, dict) and c.get('question') and c.get('answer')
    ]


def generate_cards(text: str, direction: str | None = None) -> list[dict]:
    prompt = f'Source material:\n\n{text}'
    if direction:
        prompt += f'\n\nAdditional instructions from the user: {direction}'
    return _parse_cards(chat_json(prompt, system=GENERATE_SYSTEM))


def regenerate_cards(
    question: str, answer: str, generation_context: str | None, direction: str
) -> list[dict]:
    prompt = f'Current card:\nQ: {question}\nA: {answer}\n\nUser direction: {direction}'
    if generation_context:
        prompt += f'\n\nOriginal source material:\n{generation_context}'
    return _parse_cards(chat_json(prompt, system=REGENERATE_SYSTEM))


def normalize_transcript(text: str) -> str:
    cleaned = chat_text(f'Transcript:\n\n{text}', system=NORMALIZE_SYSTEM).strip()
    return cleaned or text
