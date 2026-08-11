"""Rewriting the user-memory document to an instruction.

Deliberately *not* on the chat's decision turn. That turn is blocking and capped
(`DECISION_MAX_TOKENS`), and a tool call carrying a 4,000-character document back
to the server would truncate — and a truncated tool call returns no `tool_calls`
at all, which is indistinguishable from the model deciding it had nothing to do.
So `revise_memory` hands over a one-line *instruction*, and the expensive rewrite
happens here, in the background, where nobody is waiting on it.
"""
import logging

from backend.ai.llm import chat_text
from backend.memory import MAX_CHARS

logger = logging.getLogger(__name__)

_SYSTEM = f"""You maintain one short document of standing facts an assistant keeps \
about its user: proper names and their exact spellings, people and places, \
preferences, anything worth carrying between conversations.

You will be given the current document and one instruction describing a change. \
Apply that change and return the whole document.

Rules:
- Apply the instruction and nothing else. Never invent facts, and never drop a \
line the instruction did not ask you to touch.
- Keep it a plain list of short lines, each starting with "- ". No headings, no \
commentary, no preamble.
- Where a name has a spelling that speech-to-text tends to get wrong, keep the \
correct spelling and the mis-hearing on the same line — that pairing is what \
lets transcripts be corrected later.
- Merge duplicates and drop anything the instruction says is no longer true.
- Stay under {MAX_CHARS} characters. If it doesn't fit, consolidate the oldest, \
least specific lines first.
- Return only the document."""


def revise_memory_document(document: str, instruction: str) -> str | None:
    """The revised document, or None if the model was unavailable or unhelpful.

    None rather than a fallback: a failed revision must leave the existing
    document exactly as it was. Overwriting a page of standing facts with a
    half-answer is the failure this returns None to avoid.
    """
    instruction = (instruction or '').strip()
    if not instruction:
        return None

    prompt = (
        f'Current document:\n"""\n{document or "(empty)"}\n"""\n\n'
        f'Instruction: {instruction}'
    )
    try:
        text = (chat_text(prompt, system=_SYSTEM) or '').strip()
    except Exception as e:
        logger.warning('Memory revision failed: %s', e)
        return None

    text = _strip_fence(text)
    if not text:
        return None
    if len(text) > MAX_CHARS:
        logger.warning('Memory revision came back over the %d-character cap', MAX_CHARS)
        return None
    return text


def _strip_fence(text: str) -> str:
    """Models wrap a "return only the document" answer in a code fence often
    enough that stripping it is cheaper than re-prompting."""
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith('```') and lines[-1].strip() == '```':
        return '\n'.join(lines[1:-1]).strip()
    return text
