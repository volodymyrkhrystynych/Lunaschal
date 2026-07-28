import json
import logging
import re

from backend.ai.provider import get_provider_config, get_ollama_client, is_ai_configured, DEFAULT_MODELS
from backend.tags import normalize_tags

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


_METADATA_SYSTEM = (
    "You generate metadata for personal journal entries.\n"
    "Return ONLY valid JSON with two fields:\n"
    '- "title": a concise 4-8 word title capturing the main theme\n'
    '- "tags": an array of 1-3 tags chosen ONLY from this exact list:\n'
    "  work, health, fitness, relationships, family, finances, home, learning,\n"
    "  mood, reflection, gratitude, anxiety, motivation, growth,\n"
    "  travel, reading, creative, coding,\n"
    "  goals, plans, decisions, ideas,\n"
    "  milestone, problem, memory\n"
    'Example: {"title": "Productive morning coding session", "tags": ["work", "coding"]}'
)


def generate_journal_metadata(content: str) -> dict:
    if not content.strip():
        return {}
    try:
        if not is_ai_configured():
            return {}
        c = get_provider_config()
        client = get_ollama_client(c)
        model = c['ollama_model'] or DEFAULT_MODELS['ollama']
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': _METADATA_SYSTEM},
                {'role': 'user', 'content': content},
            ],
            response_format={'type': 'json_object'},
            stream=False,
        )
        data = json.loads(resp.choices[0].message.content)
        # normalize_tags trims, lowercases and dedupes — a model that lists
        # "reading" twice would otherwise produce two identical tags, and a
        # model that answers with a bare string instead of an array would be
        # iterated character by character.
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
        c = get_provider_config()
        system = "You are a strict binary classifier. Reply ONLY with 'yes' or 'no', nothing else."
        user = f"Does this journal entry relate to the topic '{tag_name}'?\n\n{content}"
        client = get_ollama_client(c)
        model = c['ollama_model'] or DEFAULT_MODELS['ollama']
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            stream=False,
        )
        return resp.choices[0].message.content.lower().strip().startswith('yes')
    except Exception as e:
        print(f'Tag classification failed for "{tag_name}": {e}')

    return False


def polish_journal_entry(raw_text: str) -> str:
    if not raw_text.strip():
        return raw_text
    try:
        if not is_ai_configured():
            return raw_text
        c = get_provider_config()
        client = get_ollama_client(c)
        model = c['ollama_model'] or DEFAULT_MODELS['ollama']
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': raw_text},
            ],
            stream=False,
        )
        return _clean_polish_output(resp.choices[0].message.content) or raw_text
    except Exception as e:
        logger.error('Journal polish failed, using raw text: %s', e)

    return raw_text
