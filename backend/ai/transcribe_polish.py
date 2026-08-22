"""Light speech-to-text cleanup for the general `/api/transcribe` path — the
one endpoint every dictation surface goes through (the paste/voice/journal
listener hotkeys, and every in-app microphone button via `useRecorder`).

Unlike Journal's `polish_journal_entry` (backend/ai/journal.py) or the voice
draft merge (`merge_voice_draft`), there's no time budget here for a
multi-paragraph rewrite or a multi-model cross-check — this runs inline in an
interactive request, once, against a single transcript. It only fixes
punctuation, capitalisation, and obvious mishearings; it never reformats or
reorders, since the caller may be dictating a single word into a text field.
"""
import logging
import re

from backend.ai.llm import chat_text
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You clean up a short speech-to-text transcript. The speaker's words are "
    "fixed; punctuation and capitalisation are what you are here to fix.\n"
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
    "- Break it into multiple paragraphs, or add line breaks that weren't spoken.\n"
    "\n"
    "Return only the cleaned text. No preamble, no commentary, no wrapping "
    "quotation marks."
)

_PREAMBLE_RE = re.compile(
    r"""^\s*
        (?:(?:sure|of course|certainly|okay|ok)[,!.]?\s*)?
        (?:here(?:'s|\s+is)\s+(?:your|the)\s+)?
        (?:corrected|cleaned(?:[\s-]up)?|polished|edited|revised)\s+
        (?:text|transcript|version|note)
        \s*:?\s*\n+
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WRAP_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’'), ('«', '»')]


def _clean_output(text: str) -> str:
    text = _PREAMBLE_RE.sub('', text.strip(), count=1).strip()
    for open_q, close_q in _WRAP_QUOTE_PAIRS:
        if len(text) >= 2 and text.startswith(open_q) and text.endswith(close_q):
            return text[len(open_q):-len(close_q)].strip()
    return text


def polish_transcript(raw_text: str) -> str:
    """The cleaned-up transcript, or `raw_text` unchanged if AI isn't
    configured, the call fails, or the model returns nothing usable — this
    is a best-effort pass-through, not a gate, since every dictation surface
    in the app depends on `/api/transcribe` always returning text."""
    raw_text = (raw_text or '').strip()
    if not raw_text or not is_ai_configured():
        return raw_text
    try:
        result = chat_text(raw_text, system=_SYSTEM)
    except Exception as e:
        logger.warning('Transcript polish failed: %s', e)
        return raw_text
    return _clean_output(result) or raw_text


_MERGE_SYSTEM = (
    "You clean up short speech-to-text transcripts. You will be given several "
    "independent transcriptions of the SAME recording, produced by different "
    "models — they may disagree in places because one of them misheard a word. "
    "Cross-check them against each other and decide what was most likely "
    "actually said, preferring the reading most of them agree on; where they "
    "all disagree, use your best judgement. Then return ONE clean transcript "
    "of that.\n"
    "\n"
    "Do this:\n"
    "1. Add punctuation where it is clearly missing.\n"
    "2. Capitalise the first word of each sentence.\n"
    "3. Fix spelling mistakes and obvious transcription errors beyond what the "
    "cross-check above already resolved.\n"
    "\n"
    "Never do this:\n"
    "- Remove, add, reorder, or rephrase a word beyond reconciling the "
    "transcripts against each other. Every word you keep must appear in at "
    "least one candidate, in the order it was said.\n"
    "- Improve the vocabulary, or make it sound more formal.\n"
    "- Break it into multiple paragraphs, or add line breaks that weren't spoken.\n"
    "\n"
    "Do not repeat the transcripts or their labels in your reply. Return only "
    "the cleaned text. No preamble, no commentary, no wrapping quotation marks."
)


def _format_candidates(texts: list[str]) -> str:
    return '\n\n'.join(f'Transcript {i}:\n{t}' for i, t in enumerate(texts, 1))


def merge_transcripts(texts: list[str]) -> str:
    """Reconcile several transcriptions of one recording into a single clean
    transcript, or return the first one unchanged if that isn't possible.

    The multi-model counterpart of `polish_transcript`, and it keeps that
    function's two defining properties rather than Journal's `merge_voice_draft`:
    it is **best-effort, never a gate** (every dictation surface depends on
    /api/transcribe returning text, so a dead llama-server must degrade to the
    raw transcript rather than fail the request), and it **never reformats** —
    no paragraph breaking, because the caller may be dictating a single phrase
    into a text field. `merge_voice_draft` deliberately does break paragraphs,
    which is correct for a journal entry and wrong here.

    A single candidate is polished rather than merged: cross-checking one
    transcript against itself just spends tokens telling the model to agree
    with itself.
    """
    texts = [t.strip() for t in texts if (t or '').strip()]
    if not texts:
        return ''
    if len(texts) == 1:
        return polish_transcript(texts[0])
    if not is_ai_configured():
        return texts[0]
    try:
        result = chat_text(_format_candidates(texts), system=_MERGE_SYSTEM)
    except Exception as e:
        logger.warning('Transcript merge failed: %s', e)
        return texts[0]
    return _clean_output(result) or texts[0]
