"""Shared provider-aware LLM helpers for the learning feature.

Generalizes the JSON-mode call pattern that lived in backend/ai/flashcards.py.
"""
import json

from backend.ai.provider import get_provider_config, DEFAULT_MODELS


class ToolCallingUnsupported(Exception):
    """Raised when the active provider cannot drive an OpenAI-style tool loop."""


def _openai_compat(c):
    from openai import OpenAI
    if c['provider'] == 'openai':
        return OpenAI(api_key=c['openai_api_key']), c['model'] or DEFAULT_MODELS['openai']
    return (
        OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama'),
        c['ollama_model'] or c['model'] or DEFAULT_MODELS['ollama'],
    )


def chat_json(prompt: str, system: str | None = None) -> dict:
    """Blocking JSON-mode completion; returns the parsed object."""
    c = get_provider_config()
    provider = c['provider']

    if provider in ('openai', 'ollama'):
        client, model = _openai_compat(c)
        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={'type': 'json_object'},
        )
        return json.loads(resp.choices[0].message.content)

    if provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=c['google_api_key'])
        model_name = c['model'] or DEFAULT_MODELS['gemini']
        text = f'{system}\n\n{prompt}' if system else prompt
        resp = genai.GenerativeModel(model_name).generate_content(
            text,
            generation_config={'response_mime_type': 'application/json'},
        )
        return json.loads(resp.text)

    raise ValueError(f'Unknown provider: {provider}')


def chat_text(prompt: str, system: str | None = None) -> str:
    """Blocking plain-text completion."""
    c = get_provider_config()
    provider = c['provider']

    if provider in ('openai', 'ollama'):
        client, model = _openai_compat(c)
        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': prompt})
        resp = client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content or ''

    if provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=c['google_api_key'])
        model_name = c['model'] or DEFAULT_MODELS['gemini']
        text = f'{system}\n\n{prompt}' if system else prompt
        return genai.GenerativeModel(model_name).generate_content(text).text or ''

    raise ValueError(f'Unknown provider: {provider}')


def chat_with_tools(messages: list[dict], tools: list[dict]):
    """One tool-calling turn via the OpenAI-compat API; returns the message.

    Verification's MCP loop needs OpenAI-style tool calls, which the gemini
    SDK path doesn't speak — callers surface that as providerUnsupported.
    """
    c = get_provider_config()
    if c['provider'] not in ('openai', 'ollama'):
        raise ToolCallingUnsupported(f"Provider '{c['provider']}' does not support tool calling")
    client, model = _openai_compat(c)
    resp = client.chat.completions.create(model=model, messages=messages, tools=tools)
    return resp.choices[0].message
