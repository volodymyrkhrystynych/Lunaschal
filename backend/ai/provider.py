"""Resolves the local inference endpoint: llama.cpp's `llama-server`.

`llama-server` speaks the OpenAI API, so everything in `backend/ai/` goes through
the `openai` SDK pointed at it — there is no second, native code path the way
there was for Ollama.

Model names here are **router aliases**, not file names or Ollama tags: they are
the section names in `llama/presets.ini`, which llama-server matches against the
`model` field of each request and loads on demand.
"""
import os
import openai

# Router aliases from llama/presets.ini. `DEFAULT_MODEL` is the conversational
# model; `EMBED_MODEL` is a separate small entry the router keeps resident
# alongside it (embeddings would otherwise evict the 22 GB chat model per call).
DEFAULT_MODEL = 'qwen36'
EMBED_MODEL = 'embed'


def get_settings() -> dict | None:
    from backend.db.connection import get_db
    row = get_db().execute('SELECT * FROM settings LIMIT 1').fetchone()
    return dict(row) if row else None


def get_provider_config() -> dict:
    s = get_settings()
    return {
        'llama_url': (s.get('llama_url') if s else None) or 'http://localhost:8080',
        'llama_model': s.get('llama_model') if s else None,
        # Separate alias because the chat model takes text only: images and
        # audio go to the CPU-resident [gemma4-12b-omni] preset instead — see
        # backend/ai/images.py. NULL/empty means image captioning is off, which
        # is the default until that model has been downloaded.
        'llama_vision_model': s.get('llama_vision_model') if s else None,
        # One any-to-any model serves both, so in practice this holds the same
        # alias as llama_vision_model — Settings writes them together. They stay
        # two columns because they gate two independent features, and one can
        # legitimately fail (heic, say) while the other works. See
        # backend/ai/audio_description.py. NULL/empty means it's off.
        'llama_audio_model': s.get('llama_audio_model') if s else None,
        # Whether the *chat* model itself can be handed an image, rather than
        # being read a description of one. See chat_vision_enabled below.
        'llama_chat_vision': bool(s.get('llama_chat_vision')) if s else False,
        'openai_api_key': (s.get('openai_api_key') if s else None) or os.environ.get('OPENAI_API_KEY'),
        'google_api_key': (s.get('google_api_key') if s else None) or os.environ.get('GOOGLE_API_KEY'),
    }


def get_llama_client(config: dict | None = None) -> openai.OpenAI:
    """OpenAI client bound to llama-server. `api_key` is required by the SDK but
    ignored by the server."""
    c = config or get_provider_config()
    return openai.OpenAI(base_url=f"{c['llama_url'].rstrip('/')}/v1", api_key='llama')


def get_model(config: dict | None = None) -> str:
    """The configured chat alias, or the default one."""
    c = config or get_provider_config()
    return c['llama_model'] or DEFAULT_MODEL


def chat_vision_enabled(config: dict | None = None) -> bool:
    """Whether a chat turn may carry an image rather than a description of one.

    Off by default, and it has to stay a setting rather than something inferred:
    nothing in the OpenAI API says whether the loaded preset has a projector, and
    guessing wrong means sending image parts to a model that answers about an
    image it never decoded — a failure that reads as the model hallucinating
    rather than as a misconfiguration.
    """
    c = config or get_provider_config()
    return bool(c.get('llama_chat_vision'))


def is_ai_configured() -> bool:
    try:
        return bool(get_provider_config()['llama_url'])
    except Exception:
        return False
