"""System prompt and streaming glue for the web-search chat tab.

Mirrors backend/ai/chat.py's chat_stream shape (time-awareness, createdAt
stamping) but gathers with tools before answering, via
backend/websearch/agent.py. Gathering and answering are separate turns — see
that module's docstring for why — so this yields step events for the tool
calls, then content deltas for the final answer.
"""
from backend.ai.chat import TIME_PREFIX_NOTE, format_now_context, stamp_messages
from backend.ai.llm import chat_stream_deltas
from backend.websearch import agent

SYSTEM_PROMPT = """You are Lunaschal's web-search assistant — a quick way to get \
an answer that depends on searching the internet, in place of switching to a \
browser or another chatbot.

You have tools: search the web and fetch a page's text. Use them whenever the \
answer depends on current information, specific facts, or anything you are not \
confident about from memory alone. Never guess and present it as fact — if a \
search comes up empty or a fetch fails, say so plainly.

Be direct and concise: answer the question, name what you actually read, and \
skip preamble."""

ANSWER_INSTRUCTION = (
    'Now answer using what you gathered. Name any web source you actually read. '
    'If your search came up short, say so plainly rather than filling the gap '
    'with a guess.'
)


def stream_reply(messages: list[dict]):
    """Yields ('step', event) for each tool call, then ('content', delta) for
    the streamed answer, then a final ('done', {'steps', 'sources'})."""
    system = f"{SYSTEM_PROMPT}\n\n{format_now_context()}"
    if any(m.get('createdAt') for m in messages):
        system = f"{system}\n\n{TIME_PREFIX_NOTE}"
    stamped = stamp_messages(messages)

    result: dict = {}
    for kind, payload in agent.gather_events(system, stamped):
        if kind == 'step':
            yield ('step', payload)
        else:
            result = payload

    answer_messages = result.get('messages', []) + [
        {'role': 'user', 'content': ANSWER_INSTRUCTION}
    ]
    for chunk in chat_stream_deltas(answer_messages):
        yield ('content', chunk)

    yield ('done', {'steps': result.get('steps', []), 'sources': result.get('sources', [])})
