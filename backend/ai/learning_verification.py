"""Verification support: semantic-change judgment for card revisions.

The MCP-backed evidence case builder also lives here (see build_case below,
added with the verification agent).
"""
import re

from backend.ai.llm import chat_json

SEMANTIC_SYSTEM = """You judge whether a flashcard answer edit changes the fact being tested.

Compare the old and new answers. Respond {"semantic": true} if the factual
content differs (different fact, number, mechanism, scope — anything a learner
would need to re-learn). Respond {"semantic": false} only for cosmetic edits:
typo fixes, rephrasing, formatting, reordering that leaves every fact intact.

Respond with valid JSON: {"semantic": true or false}"""


def _normalize(text: str) -> str:
    return ' '.join(re.sub(r'[^\w\s]', '', text.casefold()).split())


def judge_semantic_change(old_answer: str, new_answer: str) -> bool:
    """True when the edit changes what the learner must know (drives FSRS reset)."""
    # Cheap heuristic first: identical after case/whitespace/punctuation
    # normalization is cosmetic by construction — no LLM call.
    if _normalize(old_answer) == _normalize(new_answer):
        return False
    try:
        result = chat_json(
            f'Old answer:\n{old_answer}\n\nNew answer:\n{new_answer}',
            system=SEMANTIC_SYSTEM,
        )
        return bool(result.get('semantic', True))
    except Exception:
        # Failing safe means resetting: re-learning a card beats keeping a
        # mature schedule on content that actually changed.
        return True
