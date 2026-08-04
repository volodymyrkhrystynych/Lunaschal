"""Turning a research pass into wiki articles.

Two calls, deliberately separate: the tool loop gathers (backend/research/agent.py),
then this decides what is worth writing down. Keeping the write decision out of
the tool loop means the model is never choosing "search again" and "publish an
article" in the same breath, and the article text is produced once, from
everything gathered, rather than accreted turn by turn.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.research.idea_text import display_title

logger = logging.getLogger(__name__)

MAX_TOKENS = 4096
MAX_TRANSCRIPT_CHARS = 16000

GATHER_SYSTEM = """You research a feature idea for Lunaschal, a single-user, \
local-first life-management app, so that its owner can decide how to build it.

Use your tools to find how other people have solved this problem: prior art, \
the standard approaches, the trade-offs people report after living with them, \
and anything that would be expensive to learn the hard way.

Check your own wiki first — you may already have notes on this. Then search \
the web for what is missing.

A search result is a title and a snippet; it is not a source, and it is not \
enough to write from. Once a search turns up something substantive, open it \
with web_fetch and read it. Prefer two or three pages read properly over ten \
searches skimmed — the notes you write are only as good as the pages behind \
them, and only pages you actually opened are recorded as sources.

Do not research Lunaschal itself; you are given its inventory and it is \
authoritative."""

WRITE_SYSTEM = """You maintain a small research wiki for the owner of a \
personal app. You have just finished a research pass; now decide what is worth \
writing down.

Write articles about the *problem space*, not about this one idea — "spaced \
repetition scheduling algorithms" rather than "Volodya's flashcard idea". That \
is what makes a note useful again next year.

Rules:
- Only write what your research actually supports. If you found little, return \
no articles; an empty wiki beats a confident wrong one.
- Update an existing article by reusing its exact slug. Create a new one only \
when the topic is genuinely different from everything in the list.
- `summary` is one or two sentences and is what future retrieval sees, so make \
it say what the article is actually about.
- `content` is markdown: what the approaches are, how they differ, what the \
trade-offs are, and what you would recommend for a single-user local-first app \
running a 26B model on one 8 GB GPU.
- `note` explains, in a few words, why you changed this article — it goes into \
the revision log the owner reads."""

_WRITE_SCHEMA = {
    'type': 'object',
    'properties': {
        'articles': {
            'type': 'array',
            'maxItems': 3,
            'items': {
                'type': 'object',
                'properties': {
                    'slug': {'type': 'string'},
                    'title': {'type': 'string'},
                    'summary': {'type': 'string'},
                    'content': {'type': 'string'},
                    'note': {'type': 'string'},
                },
                'required': ['slug', 'title', 'summary', 'content'],
            },
        },
    },
    'required': ['articles'],
}


def build_gather_request(idea: dict, context: str) -> str:
    body = (idea.get('content') or idea.get('rawContent') or '')[:4000]
    return (
        f"# The idea to research\n\n{display_title(idea)}\n\n{body}\n\n"
        f'{context}\n\n'
        'Research how this kind of thing is usually done. Gather what you need, '
        'then stop — you will be asked to write it up separately.'
    )


def flatten_transcript(messages: list[dict]) -> str:
    """The gathered evidence as plain text for the write-up call.

    Tool results are what matter here; assistant turns in a gathering loop are
    mostly "I will now search for…" and carry no evidence.
    """
    parts = []
    for message in messages:
        if message.get('role') == 'tool' and message.get('content'):
            parts.append(str(message['content']))
    text = '\n\n---\n\n'.join(parts)
    # Tail-truncate: later tool results are the pages the model chose to read
    # after seeing search results, so they are the substantive ones.
    return text[-MAX_TRANSCRIPT_CHARS:]


def decide_articles(idea: dict, transcript: str, existing: list[dict]) -> list[dict]:
    """[{slug, title, summary, content, note}] — empty when nothing is worth saving."""
    if not is_ai_configured() or not transcript.strip():
        return []

    index = (
        '\n'.join(f"- {a['slug']}: {a['title']} — {a['summary']}" for a in existing)
        or '(the wiki is empty)'
    )
    prompt = (
        f"# The idea being researched\n\n{display_title(idea)}\n\n"
        f"{(idea.get('content') or idea.get('rawContent') or '')[:2000]}\n\n"
        f'# Existing wiki articles\n\n{index}\n\n'
        f'# What your research turned up\n\n{transcript}'
    )
    try:
        result = chat_json(
            prompt, system=WRITE_SYSTEM, schema=_WRITE_SCHEMA, max_tokens=MAX_TOKENS
        )
    except Exception as e:
        logger.warning('Wiki write-up failed: %s', e)
        return []

    articles = []
    seen: set[str] = set()
    for item in result.get('articles') or []:
        if not isinstance(item, dict):
            continue
        if not (item.get('slug') and item.get('title') and item.get('content')):
            continue
        # The model sometimes returns the same slug two or three times in one
        # response — three sections of one article, emitted as three articles.
        # Upserting each in turn would leave the last one standing and pile up
        # revisions of a single note, so only the first survives the pass.
        slug = item['slug']
        if slug in seen:
            logger.info('Dropped a repeated wiki slug from one write-up: %s', slug)
            continue
        seen.add(slug)
        articles.append(item)
    return articles
