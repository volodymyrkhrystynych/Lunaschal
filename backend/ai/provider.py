import os

DEFAULT_MODELS = {
    'openai': 'gpt-4o',
    'gemini': 'gemini-2.0-flash',
    'ollama': 'llama3.2',
}


def get_settings() -> dict | None:
    from backend.db.connection import get_db
    row = get_db().execute('SELECT * FROM settings LIMIT 1').fetchone()
    return dict(row) if row else None


def get_provider_config() -> dict:
    s = get_settings()
    return {
        'provider': (s.get('ai_provider') or 'openai') if s else 'openai',
        'model': s.get('ai_model') if s else None,
        'openai_api_key': (s.get('openai_api_key') if s else None) or os.environ.get('OPENAI_API_KEY'),
        'google_api_key': (s.get('google_api_key') if s else None) or os.environ.get('GOOGLE_API_KEY'),
        'ollama_url': (s.get('ollama_url') if s else None) or 'http://localhost:11434',
        'ollama_model': s.get('ollama_model') if s else None,
        'ollama_bg_model': s.get('ollama_bg_model') if s else None,
    }


def is_ai_configured() -> bool:
    try:
        c = get_provider_config()
        p = c['provider']
        if p == 'openai':
            return bool(c['openai_api_key'])
        if p == 'gemini':
            return bool(c['google_api_key'])
        if p == 'ollama':
            return bool(c['ollama_url'])
        return False
    except Exception:
        return False
