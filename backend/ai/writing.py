from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

_MAX_INPUT_CHARS = 24000

_SUMMARY_SYSTEM = (
    "You summarize a brainstorming conversation between an author and an AI assistant "
    "about the story \"{project_title}\".{description_line}\n"
    "Distill the conversation into reference notes for the author — capture the decisions "
    "made, ideas worth keeping, and open questions. Write notes, not a play-by-play of "
    "who said what.\n"
    "Return ONLY valid JSON with these fields:\n"
    '- "title": a short note title (4-8 words) describing what was discussed\n'
    '- "content": the summary as clean markdown\n'
)

_SUMMARY_SCHEMA = {
    'type': 'object',
    'properties': {'title': {'type': 'string'}, 'content': {'type': 'string'}},
    'required': ['title', 'content'],
}


def summarize_discussion(transcript: str, project_title: str, project_description: str | None = None) -> dict | None:
    """Summarize a discussion transcript into {title, content}, or None on failure."""
    if not transcript.strip():
        return None
    # Keep the tail: recent messages carry the latest decisions.
    transcript = transcript[-_MAX_INPUT_CHARS:]
    try:
        if not is_ai_configured():
            return None
        description_line = f'\nStory description: {project_description}' if project_description else ''
        system = _SUMMARY_SYSTEM.format(project_title=project_title, description_line=description_line)
        data = chat_json(transcript, system=system, schema=_SUMMARY_SCHEMA)

        title = (data.get('title') or '').strip() if isinstance(data.get('title'), str) else ''
        content = (data.get('content') or '').strip() if isinstance(data.get('content'), str) else ''
        if not title or not content:
            return None
        return {'title': title, 'content': content}
    except Exception as e:
        print(f'Discussion summarization failed: {e}')

    return None
