import logging
import re

from backend.ai.llm import chat_json, chat_text
from backend.tags import normalize_tags
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

# Written for a strict instruction-following model. The previous version led with
# "make only these changes and nothing else" and buried paragraphing as item 4
# among the prohibitions; a literal-minded model read inserting a blank line as a
# forbidden change and returned dictation as one unbroken wall of text. Hence the
# explicit split between words (untouchable) and formatting (the actual job), and
# the worked example — a demonstration lands harder than a rule.
_SYSTEM = (
    "You clean up spoken-word journal transcripts. The speaker's words are fixed; "
    "their formatting is what you are here to fix.\n"
    "\n"
    "Do all of this:\n"
    "1. Break the text into paragraphs separated by a blank line, at each shift in "
    "thought or topic. Dictation arrives as one unbroken block, so returning one "
    "unbroken block means you have not done the job — anything longer than a few "
    "sentences becomes two or more paragraphs.\n"
    "2. Add punctuation where it is clearly missing (periods, commas, question marks).\n"
    "3. Capitalise the first word of each sentence.\n"
    "4. Fix spelling mistakes and obvious transcription errors (wrong words, "
    "misheard sounds).\n"
    "\n"
    "Never do any of this:\n"
    "- Remove, add, reorder, or rephrase a word. Every word the speaker said must "
    "appear in your output, in the order they said it.\n"
    "- Improve the vocabulary, or make the text sound more formal or polished.\n"
    "\n"
    "Those restrictions cover WORDS ONLY. Line breaks, blank lines, punctuation and "
    "capitalisation are exactly what you are being asked to change; adding a blank "
    "line between two thoughts never counts as altering what the speaker said.\n"
    "\n"
    "The transcript may be followed by a line of dashes (---) and a 'Context:' "
    "section — background material such as things already known about the "
    "speaker, or descriptions of audio attached to this entry heard by a "
    "different listener. Use it only to fix a word in the transcript that is "
    "clearly a mishearing, such as a mangled name; never use it to add, remove, "
    "or infer anything the transcript doesn't already say. Do not repeat the "
    "context section or the dashes in your reply — reply with the corrected "
    "transcript only.\n"
    "\n"
    "Example input:\n"
    "so today was rough i barely slept and then the standup ran long anyway i "
    "finally got the parser working after lunch which felt great\n"
    "\n"
    "Example output:\n"
    "So today was rough. I barely slept, and then the standup ran long.\n"
    "\n"
    "Anyway, I finally got the parser working after lunch, which felt great.\n"
    "\n"
    "Return only the cleaned text. The first character of your reply must be the "
    "first character of the entry — no preamble, no commentary, nothing like "
    "'Here is the corrected text:'. Do not wrap the output, or any paragraph of it, "
    "in quotation marks; use them only where the speaker was quoting someone."
)

_PREAMBLE_RE = re.compile(
    r"""^\s*
        (?:(?:sure|of course|certainly|okay|ok)[,!.]?\s*)?
        (?:here(?:'s|\s+is)\s+(?:your|the)\s+)?
        (?:corrected|cleaned(?:[\s-]up)?|polished|edited|revised)\s+
        (?:text|transcript|version|entry)
        \s*:?\s*\n+
    """,
    re.IGNORECASE | re.VERBOSE,
)


_WRAP_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’'), ('«', '»')]


def _unwrap_quotes(paragraph: str) -> str:
    """Strip a single pair of matching quote marks that wraps an entire
    paragraph — models sometimes render "the corrected text" as a literal
    quoted string despite being told not to."""
    p = paragraph.strip()
    for open_q, close_q in _WRAP_QUOTE_PAIRS:
        if len(p) >= 2 and p.startswith(open_q) and p.endswith(close_q):
            return p[len(open_q):-len(close_q)].strip()
    return p


def _clean_polish_output(text: str) -> str:
    """Strip a leading preamble line (e.g. "Here is the corrected text:") and
    any wrapping quotation marks the model adds despite being told not to."""
    text = _PREAMBLE_RE.sub('', text.strip(), count=1).strip()
    paragraphs = text.split('\n\n')
    text = '\n\n'.join(_unwrap_quotes(p) if p.strip() else p for p in paragraphs)
    # A model that ignores "don't wrap in quotes" may wrap the entire
    # multi-paragraph reply in a single pair instead of quoting each
    # paragraph on its own — check for that only after the per-paragraph
    # pass, so paragraphs that are legitimately individually quoted aren't
    # mistaken for one big wrapped block and double-stripped.
    return _unwrap_quotes(text)


# The closed tag vocabulary. Single source of truth: it is both interpolated into
# the prompt and used as the schema enum below, so the two can't drift apart.
JOURNAL_TAGS = (
    'work', 'health', 'fitness', 'relationships', 'family', 'finances', 'home',
    'learning', 'mood', 'reflection', 'gratitude', 'anxiety', 'motivation',
    'growth', 'travel', 'reading', 'creative', 'coding', 'goals', 'plans',
    'decisions', 'ideas', 'milestone', 'problem', 'memory',
)

_METADATA_SYSTEM = (
    "You generate metadata for personal journal entries.\n"
    "Return ONLY valid JSON with two fields:\n"
    '- "title": a concise 4-8 word title capturing the main theme\n'
    '- "tags": an array of 1-3 tags chosen ONLY from this exact list:\n'
    f"  {', '.join(JOURNAL_TAGS)}\n"
    'Example: {"title": "Productive morning coding session", "tags": ["work", "coding"]}'
)

# The enum turns "chosen ONLY from this exact list" from a prompt request into a
# grammar guarantee — an off-vocabulary tag can no longer be emitted at all.
_METADATA_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'tags': {
            'type': 'array',
            'items': {'type': 'string', 'enum': list(JOURNAL_TAGS)},
            'maxItems': 3,
        },
    },
    'required': ['title', 'tags'],
}


def generate_journal_metadata(content: str) -> dict:
    if not content.strip():
        return {}
    try:
        if not is_ai_configured():
            return {}
        data = chat_json(content, system=_METADATA_SYSTEM, schema=_METADATA_SCHEMA)
        # normalize_tags dedupes, which the grammar makes necessary: constrained to
        # a short enum the model will happily emit ["problem", "problem"] to fill
        # the array, and nothing downstream would have caught it.
        valid_tags = normalize_tags(data.get('tags'))[:3]
        title = (data.get('title') or '').strip() or None
        return {'title': title, 'tags': valid_tags or None}
    except Exception as e:
        logger.error('Journal metadata generation failed: %s', e)

    return {}


def classify_entry_for_tag(content: str, tag_name: str) -> bool:
    """Returns True if the entry relates to tag_name."""
    if not content.strip():
        return False
    try:
        if not is_ai_configured():
            return False
        system = "You are a strict binary classifier. Reply ONLY with 'yes' or 'no', nothing else."
        user = f"Does this journal entry relate to the topic '{tag_name}'?\n\n{content}"
        result = chat_text(user, system=system)
        return result.lower().strip().startswith('yes')
    except Exception as e:
        print(f'Tag classification failed for "{tag_name}": {e}')

    return False


class PolishUnavailable(Exception):
    """The polish could not be produced — no AI configured, or the call to it
    failed. Distinct from "the model returned the text unchanged", which is a
    successful polish that happened to need no edits."""


def polish_journal_entry(raw_text: str, context: str | None = None) -> str:
    """Return the cleaned-up text, or raise PolishUnavailable.

    It deliberately does NOT fall back to returning `raw_text`: callers used to
    be unable to tell a real polish from a swallowed connection error, so a
    failed call would overwrite an already-polished entry with its raw
    transcript and report success. Callers decide what a failure means for them
    — the background path leaves the entry alone, the route reports 503.

    `context` — the standing memory document plus descriptions of the entry's
    attached audio/video (see backend/routes/journal.py's `_polish_context`) —
    is optional; when given, it's appended after the transcript exactly as the
    system prompt tells the model to expect, so a name the speech-to-text
    mangled can be corrected against what's already known about the speaker or
    what a different listener heard. `backend/ai/idea_polish.py`'s lighter,
    short-form counterpart and `backend/routes/stt.py`'s manual correction pass
    read the same memory document for the same reason.
    """
    if not raw_text.strip():
        return raw_text
    if not is_ai_configured():
        raise PolishUnavailable('AI is not configured')
    prompt = raw_text
    if context:
        prompt = f'{raw_text}\n\n---\nContext:\n{context}'
    try:
        result = chat_text(prompt, system=_SYSTEM)
    except Exception as e:
        logger.error('Journal polish failed: %s', e)
        raise PolishUnavailable(str(e)) from e
    cleaned = _clean_polish_output(result)
    if not cleaned:
        raise PolishUnavailable('model returned an empty polish')
    return cleaned
