"""Cleaning up a voice-captured idea: light formatting plus fixing what
speech-to-text misheard, against the standing memory document.

Ideas are short — a sentence or three — so this is not Journal's full prose
polish (paragraph breaks tuned for long dictation would be noise on a single
sentence). Corrects a word only when memory gives it a specific reason to;
with nothing to check against, the text still gets the light cleanup but no
word is rewritten on a guess.
"""
import logging
import re

from backend.ai.llm import chat_text
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You clean up a short, dictated idea for the app's own feature backlog. "
    "The author's words are fixed; punctuation and capitalisation are what "
    "you are here to fix.\n"
    "\n"
    "Do this:\n"
    "1. Add punctuation where it is clearly missing.\n"
    "2. Capitalise the first word of each sentence.\n"
    "3. Fix spelling mistakes and obvious transcription errors (wrong words, "
    "misheard sounds).\n"
    "\n"
    "Never do this:\n"
    "- Remove, add, reorder, or rephrase a word. Every word must appear in "
    "your output, in the order they were said.\n"
    "- Improve the vocabulary, or make it sound more formal.\n"
    "- Break it into multiple paragraphs — a short idea stays one block.\n"
    "\n"
    "The text may be followed by a line of dashes (---) and a 'Context:' "
    "section — things already known about the author. Use it only to fix a "
    "word that is clearly a mishearing, such as a mangled name; never use it "
    "to add, remove, or infer anything the text doesn't already say. Do not "
    "repeat the context section or the dashes in your reply.\n"
    "\n"
    "Return only the cleaned text. No preamble, no commentary, no wrapping "
    "quotation marks."
)

_PREAMBLE_RE = re.compile(
    r"""^\s*
        (?:(?:sure|of course|certainly|okay|ok)[,!.]?\s*)?
        (?:here(?:'s|\s+is)\s+(?:your|the)\s+)?
        (?:corrected|cleaned(?:[\s-]up)?|polished|edited|revised)\s+
        (?:text|idea|version|note)
        \s*:?\s*\n+
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WRAP_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’'), ('«', '»')]


def _clean_output(text: str) -> str:
    """Strip a leading preamble line and a single pair of wrapping quotes —
    the same two things a model does despite being told not to, that
    backend/ai/journal.py's polish also has to strip."""
    text = _PREAMBLE_RE.sub('', text.strip(), count=1).strip()
    for open_q, close_q in _WRAP_QUOTE_PAIRS:
        if len(text) >= 2 and text.startswith(open_q) and text.endswith(close_q):
            return text[len(open_q):-len(close_q)].strip()
    return text


def polish_idea(raw_text: str, *, memory: str = '') -> str:
    """The cleaned-up idea text, or `''` if a polish couldn't be produced.

    Unlike Journal's Polish, this never raises and has no explicit "try
    again" button to report failure to — it only ever runs once, right after
    voice capture. An empty return means `content` simply stays unset, and
    the detail pane already falls back to `raw_content` for that case
    (backend/research/CLAUDE.md's contract), so there is nothing to protect
    against a failed call overwriting.
    """
    raw_text = (raw_text or '').strip()
    if not raw_text or not is_ai_configured():
        return ''
    prompt = raw_text
    if memory and memory.strip():
        prompt = f'{raw_text}\n\n---\nContext:\n{memory.strip()}'
    try:
        result = chat_text(prompt, system=_SYSTEM)
    except Exception as e:
        logger.warning('Idea polish failed: %s', e)
        return ''
    return _clean_output(result)
