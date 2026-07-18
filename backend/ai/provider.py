import os
import openai

DEFAULT_MODELS = {
    'ollama': 'llama3.2',
}


def get_settings() -> dict | None:
    from backend.db.connection import get_db
    row = get_db().execute('SELECT * FROM settings LIMIT 1').fetchone()
    return dict(row) if row else None


def get_provider_config() -> dict:
    s = get_settings()
    return {
        'ollama_url': (s.get('ollama_url') if s else None) or 'http://localhost:11434',
        'ollama_model': s.get('ollama_model') if s else None,
        'openai_api_key': (s.get('openai_api_key') if s else None) or os.environ.get('OPENAI_API_KEY'),
        'google_api_key': (s.get('google_api_key') if s else None) or os.environ.get('GOOGLE_API_KEY'),
    }


def get_ollama_client(config: dict | None = None) -> openai.OpenAI:
    c = config or get_provider_config()
    return openai.OpenAI(base_url=f"{c['ollama_url'].rstrip('/')}/v1", api_key='ollama')


def is_ai_configured() -> bool:
    try:
        return bool(get_provider_config()['ollama_url'])
    except Exception:
        return False
