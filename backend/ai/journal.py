import json
import logging
import re

from backend.ai.provider import get_provider_config, is_ai_configured, DEFAULT_MODELS

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a minimal transcription cleaner. "
    "Make only these changes and nothing else:\n"
    "1. Fix spelling mistakes and obvious transcription errors (wrong words, misheared sounds).\n"
    "2. Add punctuation where it is clearly missing (periods, commas, question marks).\n"
    "3. Capitalise the first word of each sentence.\n"
    "4. Insert paragraph breaks (a blank line) between distinct thoughts or topic shifts, "
    "so a long stream-of-consciousness transcript reads as separate paragraphs.\n"
    "Do NOT remove any words, rephrase any sentence, reorder any sentence, improve vocabulary, "
    "or make the text sound more formal or polished. "
    "Every word the speaker said must remain in the output, in the original order. "
    "Return only the corrected text, no commentary, no lead-in phrase, and no preamble. "
    "Do NOT start your reply with anything like 'Here is the corrected text:' or 'Sure, here you go:' — "
    "the very first character of your reply must be the first character of the corrected text itself. "
    "Do NOT wrap the output — or any paragraph of it — in quotation marks. "
    "Only include a quotation mark if the speaker was themselves quoting someone."
)

_PREAMBLE_RE = re.compile(
    r"""^\s*
        (?:(?:sure|of course|certainly|okay|ok)[,!.]?\s*)?
        (?:here(?:'s|\s+is)\s+(?:your|the)\s+)?
        (?:corrected|cleaned(?:[\s-]up)?|polished|edited|revised)\s+
        (?:text|transcript|version|entry)
        \s*:?\s*\n+
    """,
    re.IGNORECASE | re.VERBOSE,
)


_WRAP_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’'), ('«', '»')]


def _unwrap_quotes(paragraph: str) -> str:
    """Strip a single pair of matching quote marks that wraps an entire
    paragraph — models sometimes render "the corrected text" as a literal
    quoted string despite being told not to."""
    p = paragraph.strip()
    for open_q, close_q in _WRAP_QUOTE_PAIRS:
        if len(p) >= 2 and p.startswith(open_q) and p.endswith(close_q):
            return p[len(open_q):-len(close_q)].strip()
    return p


def _clean_polish_output(text: str) -> str:
    """Strip a leading preamble line (e.g. "Here is the corrected text:") and
    any wrapping quotation marks the model adds despite being told not to."""
    text = _PREAMBLE_RE.sub('', text.strip(), count=1).strip()
    paragraphs = text.split('\n\n')
    return '\n\n'.join(_unwrap_quotes(p) if p.strip() else p for p in paragraphs)


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


def _ollama_client(c: dict):
    from openai import OpenAI
    return OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama')


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
            client = _ollama_client(c)
            model = c['ollama_model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _METADATA_SYSTEM},
                    {'role': 'user', 'content': content},
                ],
                response_format={'type': 'json_object'},
                stream=False,
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
    """Returns True if the entry relates to tag_name."""
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
            model = c['ollama_model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                stream=False,
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


def polish_journal_entry(raw_text: str) -> str:
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
            return _clean_polish_output(resp.choices[0].message.content) or raw_text

        elif provider == 'ollama':
            client = _ollama_client(c)
            model = c['ollama_model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _SYSTEM},
                    {'role': 'user', 'content': raw_text},
                ],
                stream=False,
            )
            return _clean_polish_output(resp.choices[0].message.content) or raw_text

        elif provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=c['google_api_key'])
            model_name = c['model'] or DEFAULT_MODELS['gemini']
            gemini = genai.GenerativeModel(model_name, system_instruction=_SYSTEM)
            resp = gemini.generate_content(raw_text)
            return _clean_polish_output(resp.text) or raw_text

    except Exception as e:
        logger.error('Journal polish failed, using raw text: %s', e)

    return raw_text
