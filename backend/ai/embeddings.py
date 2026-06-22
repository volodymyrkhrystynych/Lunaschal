from backend.ai.provider import get_provider_config, is_ai_configured

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def _split_chunks(text: str) -> list[str]:
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        if end < len(text):
            mid = start + CHUNK_SIZE // 2
            bp = max(text.rfind('.', mid, end), text.rfind('\n', mid, end))
            if bp > start:
                end = bp + 1
        chunks.append(text[start:min(end, len(text))].strip())
        start = end - CHUNK_OVERLAP
        if start >= len(text) - CHUNK_OVERLAP:
            break
    return [c for c in chunks if c]


def generate_embedding(text: str) -> list[float]:
    c = get_provider_config()
    provider = c['provider']

    if provider == 'openai':
        from openai import OpenAI
        result = OpenAI(api_key=c['openai_api_key']).embeddings.create(
            model='text-embedding-3-small', input=text
        )
        return result.data[0].embedding

    if provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=c['google_api_key'])
        return genai.embed_content(model='models/text-embedding-004', content=text)['embedding']

    if provider == 'ollama':
        from openai import OpenAI
        result = OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama').embeddings.create(
            model='nomic-embed-text', input=text
        )
        return result.data[0].embedding

    raise ValueError(f'Unknown provider: {provider}')


def generate_embeddings(text: str) -> list[dict]:
    chunks = _split_chunks(text)
    return [
        {'embedding': generate_embedding(chunk), 'chunk_index': i, 'chunk_text': chunk}
        for i, chunk in enumerate(chunks)
    ]


def is_embeddings_configured() -> bool:
    return is_ai_configured()
