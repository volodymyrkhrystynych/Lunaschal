import json
import logging

from backend.ai.provider import get_provider_config, is_ai_configured, DEFAULT_MODELS

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a minimal transcription cleaner. "
    "Make only these changes and nothing else:\n"
    "1. Fix spelling mistakes and obvious transcription errors (wrong words, misheared sounds).\n"
    "2. Add punctuation where it is clearly missing (periods, commas, question marks).\n"
    "3. Capitalise the first word of each sentence.\n"
    "Do NOT remove any words, rephrase any sentence, restructure paragraphs, improve vocabulary, "
    "or make the text sound more formal or polished. "
    "Every word the speaker said must remain in the output. "
    "Return only the corrected text, no commentary."
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


def _ollama_chat(client, model: str, messages: list, extra_body: dict | None, **kwargs):
    """Run one Ollama chat completion, raising on failure."""
    return client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        **({"extra_body": extra_body} if extra_body else {}),
        **kwargs,
    )


def _ollama_chat_with_fallback(c: dict, messages: list, prefer_bg: bool, **kwargs):
    """
    Try the bg model on CPU first; if it's unavailable fall back to the
    main model, also on CPU.  Returns the completion response.
    """
    client = _ollama_client(c)
    bg_model  = c['ollama_bg_model'] if prefer_bg else None
    main_model = c['ollama_model'] or DEFAULT_MODELS['ollama']

    if bg_model:
        try:
            logger.info("Background LLM: trying bg model '%s' on CPU", bg_model)
            return _ollama_chat(client, bg_model, messages, _CPU_OPTIONS, **kwargs)
        except Exception as e:
            logger.warning(
                "bg model '%s' unavailable (%s) — falling back to '%s' on CPU",
                bg_model, e, main_model,
            )

    logger.info("Background LLM: using main model '%s' on CPU", main_model)
    return _ollama_chat(client, main_model, messages, _CPU_OPTIONS, **kwargs)


def generate_journal_metadata(content: str) -> dict:
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
            resp = _ollama_chat_with_fallback(
                c,
                messages=[
                    {'role': 'system', 'content': _METADATA_SYSTEM},
                    {'role': 'user', 'content': content},
                ],
                prefer_bg=True,
                response_format={'type': 'json_object'},
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
        logger.error('Journal metadata generation failed: %s', e)

    return {}


def classify_entry_for_tag(content: str, tag_name: str) -> bool:
    """Returns True if the entry relates to tag_name. Background-safe (uses CPU for Ollama)."""
    if not content.strip():
        return False
    try:
        if not is_ai_configured():
            return False
        c = get_provider_config()
        provider = c['provider']
        system = "You are a strict binary classifier. Reply ONLY with 'yes' or 'no', nothing else."
        user = f"Does this journal entry relate to the topic '{tag_name}'?\n\n{content}"

        if provider == 'openai':
            from openai import OpenAI
            client = OpenAI(api_key=c['openai_api_key'])
            model = c['model'] or DEFAULT_MODELS['openai']
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                stream=False,
            )
            return resp.choices[0].message.content.lower().strip().startswith('yes')

        elif provider == 'ollama':
            client = _ollama_client(c)
            model = c['ollama_bg_model'] or c['ollama_model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                stream=False,
                extra_body=_CPU_OPTIONS,
            )
            return resp.choices[0].message.content.lower().strip().startswith('yes')

        elif provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=c['google_api_key'])
            model_name = c['model'] or DEFAULT_MODELS['gemini']
            gemini = genai.GenerativeModel(model_name, system_instruction=system)
            resp = gemini.generate_content(user)
            return resp.text.lower().strip().startswith('yes')

    except Exception as e:
        print(f'Tag classification failed for "{tag_name}": {e}')

    return False


def polish_journal_entry(raw_text: str, background: bool = False) -> str:
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
            if background:
                resp = _ollama_chat_with_fallback(
                    c,
                    messages=[
                        {'role': 'system', 'content': _SYSTEM},
                        {'role': 'user', 'content': raw_text},
                    ],
                    prefer_bg=True,
                )
            else:
                # On-demand (non-background) also runs on CPU to avoid
                # stealing VRAM from Whisper / TTS.
                client = _ollama_client(c)
                model = c['ollama_model'] or DEFAULT_MODELS['ollama']
                logger.info("On-demand polish: using '%s' on CPU", model)
                resp = _ollama_chat(
                    client, model,
                    messages=[
                        {'role': 'system', 'content': _SYSTEM},
                        {'role': 'user', 'content': raw_text},
                    ],
                    extra_body=_CPU_OPTIONS,
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
        logger.error('Journal polish failed, using raw text: %s', e)

    return raw_text
