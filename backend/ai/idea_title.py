"""Naming an idea that was only ever dictated.

Capture leaves `title` empty and the list falls back to the first line of the
transcript (`backend/research/idea_text.py`, `src/lib/ideas.ts`). That fallback
is a safety net, not a name: "so I was thinking it would be nice if the day
view had" is what a clipped first line looks like, and a backlog of those is
unreadable.

Same graceful-degrade shape as the rest of `backend/ai/`: guard with
`is_ai_configured()`, return `''` on any failure, and let the caller keep the
fallback. Nothing waits on this synchronously, so a failed call costs a name,
never an idea.
"""
import logging
import re

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 4000
# Long enough for a real noun phrase, short enough that the list stays a list.
MAX_TITLE_CHARS = 70

_SYSTEM = (
    "You name a single idea in a developer's feature backlog.\n"
    "\n"
    "You are given the idea as it was captured — often dictated, so it may "
    "ramble or start mid-thought. Produce a short title naming what the idea "
    "*is*.\n"
    "\n"
    "Rules:\n"
    "- Three to eight words. A noun phrase, not a sentence.\n"
    "- Name the specific thing, not the category: 'Auto-title new ideas', "
    "not 'Ideas improvement'.\n"
    "- Use the author's own vocabulary where they gave you one; do not invent "
    "a feature they did not describe.\n"
    "- No quotation marks, no trailing full stop, no 'Idea:' prefix.\n"
    "\n"
    "The text may be followed by a line of dashes (---) and a 'Context:' "
    "section — things already known about the author. Use it only to spell a "
    "name or a project correctly; never to add anything the idea does not say."
)

_SCHEMA = {
    'type': 'object',
    'properties': {'title': {'type': 'string'}},
    'required': ['title'],
}

_PREFIX_RE = re.compile(r'^\s*(?:idea|title)\s*:\s*', re.IGNORECASE)
_WRAP_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’')]


def clean_title(text: str) -> str:
    """Strip the label, the wrapping quotes and the trailing punctuation a model
    adds despite being told not to, then clip on a word boundary."""
    title = _PREFIX_RE.sub('', (text or '').strip()).strip()
    for open_q, close_q in _WRAP_QUOTE_PAIRS:
        if len(title) >= 2 and title.startswith(open_q) and title.endswith(close_q):
            title = title[len(open_q):-len(close_q)].strip()
    # Collapse newlines: a title is one line by definition, and a model that
    # answered with a title plus an explanation should contribute the title.
    title = title.split('\n')[0].strip()
    title = title.rstrip('.,;:').strip()
    if len(title) <= MAX_TITLE_CHARS:
        return title
    clipped = title[:MAX_TITLE_CHARS]
    last_space = clipped.rfind(' ')
    kept = clipped[:last_space] if last_space > MAX_TITLE_CHARS // 2 else clipped
    return kept.rstrip()


def generate_idea_title(text: str, *, memory: str = '') -> str:
    """A short name for the idea, or `''` when one couldn't be produced."""
    text = (text or '').strip()
    if not text or not is_ai_configured():
        return ''
    prompt = text[:MAX_INPUT_CHARS]
    if memory and memory.strip():
        prompt = f'{prompt}\n\n---\nContext:\n{memory.strip()}'
    try:
        data = chat_json(prompt, system=_SYSTEM, schema=_SCHEMA)
    except Exception as e:
        logger.warning('Idea title generation failed: %s', e)
        return ''
    title = data.get('title') if isinstance(data, dict) else None
    if not isinstance(title, str):
        return ''
    return clean_title(title)
