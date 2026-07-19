"""Post-review clarification chat: a tutor agent over one flashcard.

Unlike verification (trust-first, evidence-only), this chat is a study aid.
When an MCP knowledge source is available its tools ground the conversation;
without one the agent still explains and gives examples from the model alone.
Turns are stateless — the frontend round-trips the transcript, same as the
verification follow-up flow.
"""
import json

from backend.ai import llm

MAX_TOOL_TURNS = 8

CHAT_SYSTEM = """You are a study companion helping the user understand a flashcard \
they just reviewed.

Clarify the stored answer, explain the underlying concept, and give concrete \
examples, analogies, or mnemonics when asked. Keep replies short and focused — \
a few sentences or one small example, in markdown.

The stored answer is the card's source of truth. If you are unsure about \
something, say so instead of guessing."""

TOOLS_NOTE = """

You also have tools that query a knowledge source. Use them when the user asks \
for details, references, or examples worth looking up. When a claim comes from \
a tool result, name the source. Never invent citations."""


def build_messages(question: str, answer: str, message: str,
                   transcript: list[dict] | None = None,
                   user_answer: str | None = None,
                   with_tools: bool = False) -> list[dict]:
    """Extend the prior transcript (or start one) with the new user message."""
    if transcript:
        messages = list(transcript)
    else:
        context = f'Flashcard question: {question}\n\nStored answer: {answer}'
        if user_answer:
            context += f'\n\nThe answer the user gave during review: {user_answer}'
        system = CHAT_SYSTEM + (TOOLS_NOTE if with_tools else '')
        messages = [{'role': 'system', 'content': f'{system}\n\n{context}'}]
    messages.append({'role': 'user', 'content': message})
    return messages


def plain_turn(messages: list[dict]) -> tuple[str, list[dict]]:
    """One chat turn with no tools; returns (reply, transcript)."""
    reply = llm.chat_messages(messages)
    messages.append({'role': 'assistant', 'content': reply})
    return reply, messages


async def tool_turn(session, messages: list[dict]) -> tuple[str, list[dict]]:
    """One chat turn against an initialized MCP session, driving the tool loop."""
    from backend.ai.mcp_client import (
        mcp_tools_to_openai,
        serialize_tool_calls,
        tool_result_text,
    )

    tools = mcp_tools_to_openai((await session.list_tools()).tools)
    if not tools:
        return plain_turn(messages)

    for _ in range(MAX_TOOL_TURNS):
        msg = llm.chat_with_tools(messages, tools)
        if not msg.tool_calls:
            reply = msg.content or ''
            messages.append({'role': 'assistant', 'content': reply})
            return reply, messages
        messages.append({
            'role': 'assistant',
            'content': msg.content,
            'tool_calls': serialize_tool_calls(msg.tool_calls),
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}
            try:
                result = await session.call_tool(tc.function.name, args)
                content = tool_result_text(result)
            except Exception as e:
                content = f'Tool error: {e}'
            messages.append({'role': 'tool', 'tool_call_id': tc.id, 'content': content})

    # Tool budget exhausted: force a final plain completion so the user
    # always gets a reply.
    return plain_turn(messages)
