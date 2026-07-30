"""Text embeddings for Learning's answer-dedup hint and grading gate.

Whole answers only — the chunking helpers that used to serve the RAG vector
store went with it. Answers are short enough to embed in one call.

Served by the `embed` entry in `llama/presets.ini` — deliberately still
nomic-embed-text-v1.5, the same model Ollama served, so the float32 vectors
already stored on `learning_cards` stay comparable to newly generated ones. A
different embedding model would silently invalidate every stored vector. Without
that entry the request errors, `embed_answer` swallows it, and the dedup hint and
grading gate silently disable.
"""
from backend.ai.provider import (
    EMBED_MODEL, get_llama_client, get_provider_config, is_ai_configured,
)


def generate_embedding(text: str) -> list[float]:
    c = get_provider_config()
    client = get_llama_client(c)
    result = client.embeddings.create(model=EMBED_MODEL, input=text)
    return result.data[0].embedding


def is_embeddings_configured() -> bool:
    return is_ai_configured()
