"""Shared Ollama LLM helpers for the learning feature."""
import json

from backend.ai.provider import get_provider_config, get_ollama_client, DEFAULT_MODELS


class ToolCallingUnsupported(Exception):
    """Raised when the active provider cannot drive an OpenAI-style tool loop."""


def _messages(prompt: str, system: str | None = None) -> list[dict]:
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})
    return messages


def chat_json(prompt: str, system: str | None = None) -> dict:
    """Blocking JSON-mode completion; returns the parsed object."""
    c = get_provider_config()
    client = get_ollama_client(c)
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    resp = client.chat.completions.create(
        model=model,
        messages=_messages(prompt, system),
        response_format={'type': 'json_object'},
    )
    return json.loads(resp.choices[0].message.content)


def chat_text(prompt: str, system: str | None = None) -> str:
    """Blocking plain-text completion."""
    c = get_provider_config()
    client = get_ollama_client(c)
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    resp = client.chat.completions.create(model=model, messages=_messages(prompt, system))
    return resp.choices[0].message.content or ''


def chat_messages(messages: list[dict]) -> str:
    """Blocking plain-text completion over a prebuilt message list."""
    c = get_provider_config()
    client = get_ollama_client(c)
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    resp = client.chat.completions.create(model=model, messages=messages)
    return resp.choices[0].message.content or ''


def chat_with_tools(messages: list[dict], tools: list[dict]):
    """One tool-calling turn via the Ollama OpenAI-compat API; returns the message."""
    c = get_provider_config()
    client = get_ollama_client(c)
    model = c['ollama_model'] or DEFAULT_MODELS['ollama']
    resp = client.chat.completions.create(model=model, messages=messages, tools=tools)
    return resp.choices[0].message
