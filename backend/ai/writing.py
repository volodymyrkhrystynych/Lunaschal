import json

from backend.ai.journal import _ollama_client
from backend.ai.provider import get_provider_config, is_ai_configured, DEFAULT_MODELS

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


def summarize_discussion(transcript: str, project_title: str, project_description: str | None = None) -> dict | None:
    """Summarize a discussion transcript into {title, content}, or None on failure."""
    if not transcript.strip():
        return None
    # Keep the tail: recent messages carry the latest decisions.
    transcript = transcript[-_MAX_INPUT_CHARS:]
    try:
        if not is_ai_configured():
            return None
        c = get_provider_config()
        provider = c['provider']
        description_line = f'\nStory description: {project_description}' if project_description else ''
        system = _SUMMARY_SYSTEM.format(project_title=project_title, description_line=description_line)
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': transcript},
        ]

        if provider == 'openai':
            from openai import OpenAI
            client = OpenAI(api_key=c['openai_api_key'])
            model = c['model'] or DEFAULT_MODELS['openai']
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={'type': 'json_object'},
                stream=False,
            )
            data = json.loads(resp.choices[0].message.content)

        elif provider == 'ollama':
            client = _ollama_client(c)
            model = c['ollama_model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={'type': 'json_object'},
                stream=False,
            )
            data = json.loads(resp.choices[0].message.content)

        elif provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=c['google_api_key'])
            model_name = c['model'] or DEFAULT_MODELS['gemini']
            gemini = genai.GenerativeModel(model_name, system_instruction=system)
            resp = gemini.generate_content(
                transcript,
                generation_config={'response_mime_type': 'application/json'},
            )
            data = json.loads(resp.text)

        else:
            return None

        title = (data.get('title') or '').strip() if isinstance(data.get('title'), str) else ''
        content = (data.get('content') or '').strip() if isinstance(data.get('content'), str) else ''
        if not title or not content:
            return None
        return {'title': title, 'content': content}

    except Exception as e:
        print(f'Discussion summarization failed: {e}')

    return None
