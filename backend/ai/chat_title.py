"""Generate a short title for a day's chat conversation.

Follows the graceful-degrade convention used across backend/ai/*: guard with
is_ai_configured() and return None on any failure so the caller can fall back to
a date-based label.
"""
import json

from backend.ai.provider import get_provider_config, get_ollama_client, is_ai_configured, DEFAULT_MODELS

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
        c = get_provider_config()
        client = get_ollama_client(c)
        model = c['ollama_model'] or DEFAULT_MODELS['ollama']
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': _TITLE_SYSTEM},
                {'role': 'user', 'content': transcript},
            ],
            response_format={'type': 'json_object'},
            stream=False,
        )
        data = json.loads(resp.choices[0].message.content)
        title = data.get('title')
        if isinstance(title, str) and title.strip():
            return title.strip()
    except Exception as e:
        print(f'Chat title generation failed: {e}')
    return None
