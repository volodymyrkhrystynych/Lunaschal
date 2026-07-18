from backend.ai.provider import get_provider_config, get_ollama_client, is_ai_configured

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
    client = get_ollama_client(c)
    result = client.embeddings.create(model='nomic-embed-text', input=text)
    return result.data[0].embedding


def generate_embeddings(text: str) -> list[dict]:
    chunks = _split_chunks(text)
    return [
        {'embedding': generate_embedding(chunk), 'chunk_index': i, 'chunk_text': chunk}
        for i, chunk in enumerate(chunks)
    ]


def is_embeddings_configured() -> bool:
    return is_ai_configured()
