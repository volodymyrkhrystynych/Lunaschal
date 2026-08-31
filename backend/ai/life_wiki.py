"""Prompts and schemas for the life wiki: what the assistant knows about the user.

The sibling of `backend/ai/code_wiki.py`, and it inherits that module's one
non-negotiable rule — **write only what you actually read**. An article the
agent invented is worse than no article, because it will be retrieved later and
believed. Here it is stronger still: a wrong claim about a codebase is checked
by the next person who opens the file, and a wrong claim about a person is
checked by nobody.

**Two calls, and neither of them reads the previous prose.**

- `extract_facts` turns a window of the user's own record into short statements,
  each citing the row it came from and naming the article it belongs to.
- `write_article` renders one article from its *fact list*.

That second point is the design. Revising prose into prose, night after night,
is the mechanism shown to accumulate distortions the model cannot detect —
each rewrite compounding the last one's loss. Rendering from facts means the Nth
render reads N facts, not N-1 renders.
"""
import logging
import re

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

EXTRACT_MAX_TOKENS = 3072
WRITE_MAX_TOKENS = 2048

# Ceiling on what one extraction may produce. A model handed a busy week will
# otherwise write down every sandwich; the cap forces it to pick.
MAX_FACTS_PER_PASS = 24

_CITATION = re.compile(r'^(journal|message|food|workout|calendar|observation):(.+)$')

EXTRACT_SYSTEM = """You keep a small wiki about one person, for an assistant \
that talks to them every day. You are reading what they recorded recently and \
writing down what is worth still knowing next month.

A fact is worth recording when it is **durable**: a standing preference, a \
routine, a person or place that recurs, a constraint they live with, something \
they are working towards, a taste they have shown more than once.

Do not record:
- anything that happened once and is over ("had ramen on Tuesday"),
- a passing mood or a single bad night,
- anything already covered by an existing fact you were shown,
- anything you are inferring rather than reading. If they did a thing twice, \
that is two events, not a habit — wait.

Every fact must cite the entry it came from, using the exact bracketed id shown \
beside that entry, and must be one plain sentence written about them in the \
third person.

Group facts under an article. Reuse an existing slug wherever one fits — you \
are shown the list. Only invent a slug when a fact genuinely belongs to no \
existing article, and make it broad enough to hold the ones that follow \
("health-and-training", not "tuesday-gym-session")."""

_EXTRACT_SCHEMA = {
    'type': 'object',
    'properties': {
        'facts': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'slug': {
                        'type': 'string',
                        'description': 'Existing article slug, or a new broad one.',
                    },
                    'title': {
                        'type': 'string',
                        'description': "The article's title, for a new slug.",
                    },
                    'statement': {
                        'type': 'string',
                        'description': 'One sentence, third person, about the user.',
                    },
                    'source': {
                        'type': 'string',
                        'description': 'The bracketed id, e.g. journal:01J4X…',
                    },
                },
                'required': ['slug', 'statement', 'source'],
            },
        },
    },
    'required': ['facts'],
}

WRITE_SYSTEM = """You are writing one article of a small wiki about one person, \
from a list of facts that have been recorded about them.

The facts are the record; this article is a readable view of them. So:

- **Use only the facts you were given.** Do not add, extend, soften or infer \
past them. If the list is thin, the article is short — that is correct, not a \
failure to try.
- Write plainly, in the third person, as notes an assistant keeps to be useful \
rather than a profile written to flatter. No summarising flourishes, no \
"clearly they value…".
- Where facts disagree, say so rather than picking one. A person who said two \
different things is a fact about that person.
- `summary` is one sentence and is what the assistant sees before deciding \
whether to open the article, so make it say what is actually in it."""

_WRITE_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'summary': {'type': 'string'},
        'content': {'type': 'string'},
        'supersedes': {
            'type': 'array',
            'description': (
                'Ids of facts that a newer fact in the list has made untrue. '
                'Only for a direct contradiction, never for something that has '
                'merely gone quiet.'
            ),
            'items': {'type': 'string'},
        },
    },
    'required': ['title', 'summary', 'content'],
}


def parse_citation(source: str) -> tuple[str, str] | None:
    """`journal:01J…` -> ('journal', '01J…'), or None if it is not one.

    A fact whose citation does not parse is dropped by the caller rather than
    stored uncited: an uncitable fact cannot be checked by the user or rebuilt
    from source, which is the whole contract the fact table exists to keep.
    """
    match = _CITATION.match((source or '').strip().strip('[]'))
    if not match:
        return None
    kind, source_id = match.group(1), match.group(2).strip()
    return (kind, source_id) if source_id else None


def build_extract_request(digest_text: str, index_lines: str) -> str:
    parts = []
    if index_lines:
        parts.append(
            '# Articles that already exist\n\n' + index_lines
            + '\n\nReuse one of these slugs wherever a fact fits it.'
        )
    else:
        parts.append(
            '# Articles that already exist\n\nNone yet — this is the first pass.'
        )
    parts.append('# What they recorded\n\n' + digest_text)
    parts.append(
        f'Write down what is worth still knowing next month, at most '
        f'{MAX_FACTS_PER_PASS} facts. Cite every one. If nothing here is '
        'durable, return an empty list — that is a normal outcome for a quiet '
        'day.'
    )
    return '\n\n'.join(parts)


def extract_facts(digest_text: str, index_lines: str = '') -> list[dict]:
    """Facts from one window, or [] on any failure.

    [] is a real outcome and not an error: most days add nothing that will still
    matter in a month, and a pass that invents something to justify itself is
    the failure mode this whole design is arranged against.
    """
    if not is_ai_configured() or not digest_text.strip():
        return []
    try:
        data = chat_json(
            build_extract_request(digest_text, index_lines),
            system=EXTRACT_SYSTEM,
            schema=_EXTRACT_SCHEMA,
            max_tokens=EXTRACT_MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Life-wiki extraction failed: %s', e)
        return []

    facts = data.get('facts')
    if not isinstance(facts, list):
        return []
    return [f for f in facts[:MAX_FACTS_PER_PASS] if isinstance(f, dict)]


def build_write_request(slug: str, existing_title: str | None,
                        facts_text: str) -> str:
    heading = f'# The article\n\n`{slug}`'
    if existing_title:
        heading += f', currently titled "{existing_title}"'
    return '\n\n'.join([
        heading,
        '# Everything recorded about it\n\n' + facts_text,
        'Write the article from these facts and nothing else. If any of them '
        'has been made untrue by a later one in the same list, put its id in '
        '`supersedes`.',
    ])


def write_article(slug: str, existing_title: str | None,
                  facts_text: str) -> dict | None:
    """{title, summary, content, supersedes} for the article, or None.

    None where `code_wiki.write_article` returns None: the call failed, or there
    was nothing to write from. The caller leaves the article as it stands.
    """
    if not is_ai_configured() or not facts_text.strip():
        return None
    try:
        data = chat_json(
            build_write_request(slug, existing_title, facts_text),
            system=WRITE_SYSTEM,
            schema=_WRITE_SCHEMA,
            max_tokens=WRITE_MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Life-wiki write failed for %s: %s', slug, e)
        return None
    if not (data.get('content') or '').strip():
        return None
    return data
