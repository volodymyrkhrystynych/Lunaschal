"""A short summary of a merged pull request, for its journal entry.

PR bodies here are mostly long write-ups (task, plan, tests), which is too much
to read in a day's feed; the entry wants what changed and why, in a sentence
or three. Returns '' when there is nothing to summarise or no model — the
entry is complete without it. `InferencePaused`/`Preempted` propagate so the
job is requeued rather than recorded as done.
"""
from backend.ai.llm import chat_text
from backend.ai.provider import is_ai_configured

# Enough for the substance of the longest write-ups; the tail is test logs.
MAX_BODY_CHARS = 8000

_SYSTEM = (
    'You summarise a merged pull request for a personal journal kept by the '
    "repository's owner. Write one to three plain sentences, in the past "
    'tense, saying what changed in the app and why. No markdown, no headings, '
    'no bullet points, no PR number, and no mention of tests, CI or who wrote '
    'it. Reply with the summary only.'
)


def summarize_pr(title: str, body: str) -> str:
    body = (body or '').strip()
    if not body or not is_ai_configured():
        return ''
    prompt = f'Title: {title}\n\n{body[:MAX_BODY_CHARS]}'
    return chat_text(prompt, system=_SYSTEM).strip()
