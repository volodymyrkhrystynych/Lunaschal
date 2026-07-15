import time
from datetime import datetime

from backend.ai.provider import get_provider_config, DEFAULT_MODELS

SYSTEM_PROMPT = """You are Lunaschal, a warm, curious companion the user chats with throughout the day.

Talk like a good friend rather than a product: react to what the user says, ask natural
follow-up questions, and share a genuine take when asked. Keep replies short and
conversational — a couple of sentences unless the user clearly wants depth. Don't list
your capabilities or turn every message into a task; it's fine for a chat to just be a chat.

If the user mentions something worth keeping — a memory, a plan, something they learned —
you may gently offer to save it, but never push.

If journal entries from the last 24 hours are included below, treat them as things the
user has recently been living and thinking about. Let them inform the conversation and
follow up on them naturally when relevant, but don't recite them back or announce that
you can see them.

When extra context from the user's knowledge base is provided, weave it in naturally."""

JOURNAL_WINDOW_SECONDS = 86400
JOURNAL_MAX_ENTRIES = 10
JOURNAL_MAX_CHARS = 2000


def get_recent_journal_entries(now: int | None = None) -> list[dict]:
    """Journal entries from the last 24 hours, excluding fanfic-commentary
    entries (those linked via journal_entry_fic_refs), oldest first."""
    from backend.db.connection import get_db
    now = now if now is not None else int(time.time())
    rows = get_db().execute(
        '''SELECT title, content, created_at FROM journal_entries
           WHERE created_at >= ?
             AND id NOT IN (SELECT journal_entry_id FROM journal_entry_fic_refs)
           ORDER BY created_at DESC LIMIT ?''',
        (now - JOURNAL_WINDOW_SECONDS, JOURNAL_MAX_ENTRIES),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _format_entry_time(ts: int, now: int) -> str:
    dt = datetime.fromtimestamp(ts)
    days = (datetime.fromtimestamp(now).date() - dt.date()).days
    day = 'today' if days == 0 else 'yesterday' if days == 1 else dt.strftime('%b %d')
    return f"{day} {dt.strftime('%H:%M')}"


def format_journal_context(entries: list[dict], now: int | None = None) -> str:
    if not entries:
        return ''
    now = now if now is not None else int(time.time())
    parts = []
    for e in entries:
        content = e['content']
        if len(content) > JOURNAL_MAX_CHARS:
            content = content[:JOURNAL_MAX_CHARS] + '…'
        header = f"[{_format_entry_time(e['created_at'], now)}]"
        if e.get('title'):
            header += f" {e['title']}"
        parts.append(f"{header}\n{content}")
    return (
        "Here is what the user wrote in their journal over the last 24 hours "
        "(oldest first):\n\n" + '\n\n'.join(parts)
    )


def build_chat_system_prompt(now: int | None = None) -> str:
    context = format_journal_context(get_recent_journal_entries(now), now)
    return f"{SYSTEM_PROMPT}\n\n{context}" if context else SYSTEM_PROMPT


def chat_stream(messages: list[dict], rag_context: str = '', system_prompt: str = ''):
    c = get_provider_config()
    provider = c['provider']

    system = system_prompt or SYSTEM_PROMPT
    if rag_context:
        system = f"{system}\n\n{rag_context}"

    all_messages = [{'role': 'system', 'content': system}] + messages

    if provider in ('openai', 'ollama'):
        from openai import OpenAI
        if provider == 'openai':
            client = OpenAI(api_key=c['openai_api_key'])
            model = c['model'] or DEFAULT_MODELS['openai']
        else:
            client = OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama')
            model = c['ollama_model'] or c['model'] or DEFAULT_MODELS['ollama']

        stream = client.chat.completions.create(model=model, messages=all_messages, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    elif provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=c['google_api_key'])
        model_name = c['model'] or DEFAULT_MODELS['gemini']
        gemini = genai.GenerativeModel(model_name, system_instruction=system)
        gemini_msgs = [
            {'role': 'user' if m['role'] == 'user' else 'model', 'parts': [m['content']]}
            for m in messages
        ]
        response = gemini.generate_content(gemini_msgs, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text
