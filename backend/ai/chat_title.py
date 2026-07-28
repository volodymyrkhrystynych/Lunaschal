"""Generate a short title for a day's chat conversation.

Follows the graceful-degrade convention used across backend/ai/*: guard with
is_ai_configured() and return None on any failure so the caller can fall back to
a date-based label.
"""
from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

_MAX_INPUT_CHARS = 16000

_TITLE_SYSTEM = (
    "You name a day's chat between a user and their personal AI assistant.\n"
    "Read the transcript and produce a concise 4-8 word title capturing what the "
    "day's conversation was mostly about. No quotes, no trailing punctuation.\n"
    'Return ONLY valid JSON: {"title": "<the title>"}'
)


def _transcript(messages: list[dict]) -> str:
    """Flatten user/assistant messages into a plain transcript, skipping system
    rows and break markers."""
    lines = []
    for m in messages:
        role = m.get('role')
        if role not in ('user', 'assistant'):
            continue
        content = (m.get('content') or '').strip()
        if not content:
            continue
        who = 'User' if role == 'user' else 'Assistant'
        lines.append(f'{who}: {content}')
    return '\n'.join(lines)


def generate_conversation_title(messages: list[dict]) -> str | None:
    """Return a short title for the conversation, or None if unconfigured/empty/failed."""
    transcript = _transcript(messages)
    if not transcript.strip():
        return None
    # Keep the tail: the latest exchanges best characterize the day.
    transcript = transcript[-_MAX_INPUT_CHARS:]
    try:
        if not is_ai_configured():
            return None
        data = chat_json(transcript, system=_TITLE_SYSTEM)
        title = data.get('title')
        if isinstance(title, str) and title.strip():
            return title.strip()
    except Exception as e:
        print(f'Chat title generation failed: {e}')
    return None
