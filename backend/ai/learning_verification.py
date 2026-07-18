"""Verification: MCP-grounded evidence cases and semantic-change judgment.

Trust-first, no-fallback: the case is built ONLY from the folder's bound
evidence provider. When the provider yields nothing usable the verdict is
notFound — verification never quietly degrades into open-web guessing.
"""
import json
import re

from backend.ai.llm import chat_json

MAX_TOOL_TURNS = 8

VERIFY_SYSTEM = """You are verifying a flashcard against an authoritative source.

You have tools that query the authoritative source bound to this card's folder.
Use them to check whether the stored answer is correct, current, and complete.
Rules:
- Base every claim you make ONLY on tool results from this session. Never use
  your own prior knowledge as evidence, and never claim to have consulted
  anything you did not retrieve through the tools.
- Every claim in your case must carry a citation quoting the tool result it
  came from.
- If the tools return nothing that settles the question, the verdict is
  "notFound". Do not guess.

When you are done researching, respond with ONLY valid JSON (no prose, no code
fences):
{"verdict": "supports" | "contradicts" | "partial" | "notFound",
 "summary": "one-paragraph case addressed to the user",
 "proposedAnswer": "corrected answer text, only when verdict is contradicts or partial",
 "citations": [{"title": "...", "source": "tool name or document", "quote": "verbatim quote"}]}"""

FOLLOWUP_INSTRUCTION = (
    'Answer the follow-up question using the tools, under the same rules. '
    'Respond with the same JSON shape when done.'
)


def _parse_case(content: str) -> dict | None:
    """Lenient JSON extraction from the model's final message."""
    if not content:
        return None
    text = content.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-z]*\n?|```$', '', text, flags=re.MULTILINE).strip()
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        case = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if case.get('verdict') not in ('supports', 'contradicts', 'partial', 'notFound'):
        return None
    case.setdefault('summary', '')
    case.setdefault('citations', [])
    return case


def _serialize_tool_calls(tool_calls) -> list[dict]:
    return [
        {
            'id': tc.id,
            'type': 'function',
            'function': {'name': tc.function.name, 'arguments': tc.function.arguments},
        }
        for tc in tool_calls
    ]


async def build_case(
    session,
    question: str,
    answer: str,
    followup: str | None = None,
    transcript: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Drive the tool loop against an initialized MCP session.

    Returns (case, transcript). The transcript is the full JSON-serializable
    message list; follow-ups are stateless — the frontend sends it back.
    """
    from backend.ai.llm import chat_with_tools
    from backend.ai.mcp_client import mcp_tools_to_openai, tool_result_text

    tools = mcp_tools_to_openai((await session.list_tools()).tools)
    if not tools:
        return {'verdict': 'notFound', 'summary': 'The evidence provider exposes no tools.',
                'citations': []}, transcript or []

    if transcript:
        messages = list(transcript)
    else:
        messages = [
            {'role': 'system', 'content': VERIFY_SYSTEM},
            {'role': 'user', 'content': f'Flashcard question: {question}\n\nStored answer: {answer}'},
        ]
    if followup:
        messages.append({'role': 'user', 'content': f'{followup}\n\n{FOLLOWUP_INSTRUCTION}'})

    case = None
    for _ in range(MAX_TOOL_TURNS):
        msg = chat_with_tools(messages, tools)
        if msg.tool_calls:
            messages.append({
                'role': 'assistant',
                'content': msg.content,
                'tool_calls': _serialize_tool_calls(msg.tool_calls),
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
            continue
        messages.append({'role': 'assistant', 'content': msg.content})
        case = _parse_case(msg.content)
        if case:
            break
        messages.append({'role': 'user', 'content':
                         'Respond with ONLY the JSON object in the required shape.'})

    if case is None:
        case = {'verdict': 'notFound',
                'summary': 'The evidence provider returned nothing conclusive.',
                'citations': []}
    return case, messages

SEMANTIC_SYSTEM = """You judge whether a flashcard answer edit changes the fact being tested.

Compare the old and new answers. Respond {"semantic": true} if the factual
content differs (different fact, number, mechanism, scope — anything a learner
would need to re-learn). Respond {"semantic": false} only for cosmetic edits:
typo fixes, rephrasing, formatting, reordering that leaves every fact intact.

Respond with valid JSON: {"semantic": true or false}"""


def _normalize(text: str) -> str:
    return ' '.join(re.sub(r'[^\w\s]', '', text.casefold()).split())


def judge_semantic_change(old_answer: str, new_answer: str) -> bool:
    """True when the edit changes what the learner must know (drives FSRS reset)."""
    # Cheap heuristic first: identical after case/whitespace/punctuation
    # normalization is cosmetic by construction — no LLM call.
    if _normalize(old_answer) == _normalize(new_answer):
        return False
    try:
        result = chat_json(
            f'Old answer:\n{old_answer}\n\nNew answer:\n{new_answer}',
            system=SEMANTIC_SYSTEM,
        )
        return bool(result.get('semantic', True))
    except Exception:
        # Failing safe means resetting: re-learning a card beats keeping a
        # mature schedule on content that actually changed.
        return True
