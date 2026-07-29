"""Text embeddings for Learning's answer-dedup hint and grading gate.

Whole answers only — the chunking helpers that used to serve the RAG vector
store went with it. Answers are short enough to embed in one call.

Requires the `nomic-embed-text` model to be pulled in Ollama; without it the
request 404s, `embed_answer` swallows the error, and the dedup hint and grading
gate silently disable.
"""
from backend.ai.provider import get_provider_config, get_ollama_client, is_ai_configured

EMBED_MODEL = 'nomic-embed-text'


def generate_embedding(text: str) -> list[float]:
    c = get_provider_config()
    client = get_ollama_client(c)
    result = client.embeddings.create(model=EMBED_MODEL, input=text)
    return result.data[0].embedding


def is_embeddings_configured() -> bool:
    return is_ai_configured()
