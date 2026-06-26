from backend.ai.provider import get_provider_config, is_ai_configured, DEFAULT_MODELS

_SYSTEM = (
    "You are lightly cleaning up a spoken journal entry. "
    "Only remove filler words (um, uh, like, you know, sort of, kind of) and fix obvious transcription errors. "
    "Do NOT rephrase, restructure, or add anything. Keep every word the speaker used. "
    "Preserve the original sentence structure and voice exactly. "
    "Return only the cleaned text, no commentary."
)


def polish_journal_entry(raw_text: str) -> str:
    if not raw_text.strip():
        return raw_text
    try:
        if not is_ai_configured():
            return raw_text
        c = get_provider_config()
        provider = c['provider']

        if provider in ('openai', 'ollama'):
            from openai import OpenAI
            if provider == 'openai':
                client = OpenAI(api_key=c['openai_api_key'])
                model = c['model'] or DEFAULT_MODELS['openai']
            else:
                client = OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama')
                model = c['ollama_model'] or c['model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': _SYSTEM},
                    {'role': 'user', 'content': raw_text},
                ],
                stream=False,
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
