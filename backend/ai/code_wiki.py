"""Prompts and schema for writing a wiki note about one module of a codebase.

The sibling of backend/ai/idea_research.py, and the differences are the point.
That one researches a *problem space* from the web and is told to write about
the space rather than the idea, so the note is useful again next year. This one
reads *one module of one repository* and is told the opposite: be specific, name
files and functions, and record the things that are true of this code and no
other.

Both share the rule that matters — write only what you actually looked at. An
article the agent invented is worse than no article, because it will be
retrieved later and believed.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_TOKENS = 3072
MAX_TRANSCRIPT_CHARS = 24000

GATHER_SYSTEM = """You are reading one part of a codebase so you can write a \
short reference note about it.

Work like someone new to the repository who has to explain this module to the \
next person:
- Start by listing the directory, then read the files that look central.
- Follow what actually happens. If a function calls something elsewhere, go and \
look at it.
- Prefer reading a file over guessing from its name.

You are gathering only. Do not write the note yet — you will be asked for it \
separately. Stop when you could explain what this module does and how it fits \
into the rest of the repository."""

WRITE_SYSTEM = """You maintain a code wiki for one repository: one short note \
per module, for an experienced developer who will use it to plan changes.

Write about *this* code, specifically. The sibling research wiki covers general \
problem spaces; this one is for the things that are true of this repository and \
nowhere else.

Rules:
- **Only write what you actually read.** If you saw little of the module, say \
less. An invented note is worse than no note, because it will be retrieved \
later and believed.
- Cite real locations as `path/to/file.py:123` in the prose. That is what makes \
the note actionable rather than decorative.
- Lead with what the module is *for*, then how it is put together, then the \
things that would surprise someone changing it — invariants, ordering \
requirements, and anything that looks odd but is deliberate.
- Do not list every function. A file listing is already available and costs \
nothing to regenerate; judgement is what a note adds.
- `summary` is one or two sentences and is what future retrieval sees, so make \
it say what the module actually does.
- `note` explains in a few words why you changed the article — it goes into the \
revision log the owner reads."""

_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'summary': {'type': 'string'},
        'content': {'type': 'string'},
        'note': {'type': 'string'},
        # Not a free-text field on purpose: it is only read when the model says
        # it did not see enough, and a boolean cannot be hedged.
        'insufficient': {
            'type': 'boolean',
            'description': 'True if you did not read enough to write a useful note.',
        },
    },
    'required': ['title', 'summary', 'content'],
}


def build_gather_request(
    repo_name: str,
    module_path: str,
    module_info: dict | None = None,
    existing: dict | None = None,
) -> str:
    """The brief for the reading loop."""
    where = module_path or 'the repository root'
    parts = [f'# The module to read\n\n`{where}` in the **{repo_name}** repository.']

    if module_info:
        parts.append(
            f"It holds {module_info.get('files', '?')} source files, "
            f"{module_info.get('lines', 0):,} lines "
            f"({', '.join(module_info.get('languages') or []) or 'mixed'})."
        )
    if existing:
        # Shown so a refresh reads as an update rather than a rewrite from
        # nothing, and so the model can go looking for what changed.
        parts.append(
            '# The note that exists today\n\n'
            f"{existing.get('content') or ''}\n\n"
            'Read the module again and find what this no longer describes.'
        )

    parts.append(
        f'Read `{where}` now. List it, open the files that matter, and follow '
        'anything important that leads outside it.'
    )
    return '\n\n'.join(parts)


def flatten_transcript(messages: list[dict]) -> str:
    """The gathered material as plain text for the write-up call.

    Tool results are what matter; assistant turns in a gathering loop are mostly
    "I will now read…" and carry nothing. Tail-truncated, because the later
    reads are the files the model chose after seeing the directory.
    """
    parts = [
        str(message['content'])
        for message in messages
        if message.get('role') == 'tool' and message.get('content')
    ]
    return '\n\n---\n\n'.join(parts)[-MAX_TRANSCRIPT_CHARS:]


def write_article(
    repo_name: str,
    module_path: str,
    transcript: str,
    files_read: list[str] | None = None,
) -> dict | None:
    """{title, summary, content, note} for the module, or None to write nothing.

    None is a real, expected outcome: a module the loop could not get into, or
    one too thin to be worth a note. Returning nothing beats filling the wiki
    with confident paraphrase.
    """
    if not is_ai_configured() or not transcript.strip():
        return None

    where = module_path or 'the repository root'
    read_list = '\n'.join(f'- {f}' for f in (files_read or [])) or '(none)'
    prompt = (
        f'# The module\n\n`{where}` in **{repo_name}**\n\n'
        f'# Files you actually opened\n\n{read_list}\n\n'
        f'# What you read\n\n{transcript}'
    )
    try:
        result = chat_json(
            prompt, system=WRITE_SYSTEM, schema=_SCHEMA, max_tokens=MAX_TOKENS
        )
    except Exception as e:
        logger.warning('Code-wiki write-up failed for %s: %s', module_path, e)
        return None

    if not isinstance(result, dict) or result.get('insufficient'):
        return None
    title = (result.get('title') or '').strip()
    content = (result.get('content') or '').strip()
    if not title or not content:
        return None
    return {
        'title': title,
        'summary': (result.get('summary') or '').strip(),
        'content': content,
        'note': (result.get('note') or '').strip() or None,
    }
