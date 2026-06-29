import json

from backend.ai.provider import get_provider_config, is_ai_configured, DEFAULT_MODELS

_SYSTEM = (
    "You are lightly cleaning up a spoken journal entry. "
    "Only remove filler words (um, uh, like, you know, sort of, kind of) and fix obvious transcription errors. "
    "Do NOT rephrase, restructure, or add anything. Keep every word the speaker used. "
    "Preserve the original sentence structure and voice exactly. "
    "Return only the cleaned text, no commentary."
)


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

# Passed to Ollama when the call should run entirely on CPU (no VRAM consumed).
_CPU_OPTIONS = {"options": {"num_gpu": 0}}


def _ollama_client(c: dict):
    from openai import OpenAI
    return OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama')


def generate_journal_metadata(content: str) -> dict:
    """Background task — always uses the bg model on CPU when Ollama is active."""
    if not content.strip():
        return {}
    try:
        if not is_ai_configured():
            return {}
        c = get_provider_config()
        provider = c['provider']

        if provider == 'openai':
            from openai import OpenAI
            client = OpenAI(api_key=c['openai_api_key'])
            model = c['model'] or DEFAULT_MODELS['openai']
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _METADATA_SYSTEM},
                    {'role': 'user', 'content': content},
                ],
                response_format={'type': 'json_object'},
                stream=False,
            )

        elif provider == 'ollama':
            client = _ollama_client(c)
            # Prefer the dedicated bg model; fall back to main model but still run on CPU
            model = c['ollama_bg_model'] or c['ollama_model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _METADATA_SYSTEM},
                    {'role': 'user', 'content': content},
                ],
                response_format={'type': 'json_object'},
                stream=False,
                extra_body=_CPU_OPTIONS,
            )

        elif provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=c['google_api_key'])
            model_name = c['model'] or DEFAULT_MODELS['gemini']
            gemini = genai.GenerativeModel(model_name, system_instruction=_METADATA_SYSTEM)
            resp = gemini.generate_content(
                content,
                generation_config={'response_mime_type': 'application/json'},
            )
            data = json.loads(resp.text)
            valid_tags = [t.strip() for t in (data.get('tags') or []) if isinstance(t, str) and t.strip()][:3]
            title = (data.get('title') or '').strip() or None
            return {'title': title, 'tags': valid_tags or None}

        else:
            return {}

        data = json.loads(resp.choices[0].message.content)
        valid_tags = [t.strip() for t in (data.get('tags') or []) if isinstance(t, str) and t.strip()][:3]
        title = (data.get('title') or '').strip() or None
        return {'title': title, 'tags': valid_tags or None}

    except Exception as e:
        print(f'Journal metadata generation failed: {e}')

    return {}


def polish_journal_entry(raw_text: str, background: bool = False) -> str:
    """
    Clean up a spoken journal entry.

    background=True  → use the bg model on CPU (called from _polish_bg thread).
    background=False → use the main model on GPU (called from the on-demand /polish endpoint).
    """
    if not raw_text.strip():
        return raw_text
    try:
        if not is_ai_configured():
            return raw_text
        c = get_provider_config()
        provider = c['provider']

        if provider == 'openai':
            from openai import OpenAI
            client = OpenAI(api_key=c['openai_api_key'])
            model = c['model'] or DEFAULT_MODELS['openai']
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _SYSTEM},
                    {'role': 'user', 'content': raw_text},
                ],
                stream=False,
            )
            return resp.choices[0].message.content.strip() or raw_text

        elif provider == 'ollama':
            client = _ollama_client(c)
            if background and c['ollama_bg_model']:
                model = c['ollama_bg_model']
                extra = _CPU_OPTIONS
            else:
                model = c['ollama_model'] or DEFAULT_MODELS['ollama']
                extra = {}
            kwargs = {'extra_body': extra} if extra else {}
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _SYSTEM},
                    {'role': 'user', 'content': raw_text},
                ],
                stream=False,
                **kwargs,
            )
            return resp.choices[0].message.content.strip() or raw_text

        elif provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=c['google_api_key'])
            model_name = c['model'] or DEFAULT_MODELS['gemini']
            gemini = genai.GenerativeModel(model_name, system_instruction=_SYSTEM)
            resp = gemini.generate_content(raw_text)
            return resp.text.strip() or raw_text

    except Exception as e:
        print(f'Journal polish failed, using raw text: {e}')

    return raw_text
