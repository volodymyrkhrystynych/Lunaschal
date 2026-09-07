"""Durable summaries for long Chat segments and New Chat handoffs.

Messages are always the source of truth. A compaction only replaces them in an
LLM prompt; it never edits or deletes transcript rows.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterable

from ulid import ULID

from backend.ai.llm import chat_json
from backend.db.connection import get_db

logger = logging.getLogger(__name__)

# Conservative defaults for the local 190K-token server. These are estimates,
# not model limits; output, system prompt and tool transcripts need headroom.
SOFT_LIMIT_TOKENS = 120_000
HARD_LIMIT_TOKENS = 170_000
RECENT_TARGET_TOKENS = 60_000
CHARS_PER_TOKEN = 4
MAX_HANDOFF_CHARS = 12_000
MAX_EVIDENCE_CHARS = 12_000

SUMMARY_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'facts': {'type': 'array', 'items': {'type': 'string'}},
        'decisions': {'type': 'array', 'items': {'type': 'string'}},
        'openThreads': {'type': 'array', 'items': {'type': 'string'}},
        'sources': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'url': {'type': 'string'},
                },
                'required': ['title', 'url'],
            },
        },
    },
    'required': ['summary', 'facts', 'decisions', 'openThreads', 'sources'],
}

SUMMARY_SYSTEM = """Compress a conversation without changing its meaning.
Preserve durable facts, exact names, corrections, decisions, commitments, open
questions, disagreements, and research conclusions with their source title and
URL. Prefer precise compact statements. Drop greetings, repetition, abandoned
reasoning, and tool plumbing. Do not invent or resolve anything that the
conversation left uncertain."""


def _metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _content(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def estimate_tokens(messages: Iterable[dict]) -> int:
    chars = sum(len(_content(m.get('content', ''))) + 16 for m in messages)
    return (chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _render(messages: Iterable[dict], previous: dict | None = None) -> str:
    parts = []
    if previous:
        parts.append('Existing summary of still-earlier messages:\n' + json.dumps(
            previous, ensure_ascii=False,
        ))
    for message in messages:
        role = message.get('role', 'unknown')
        if role == 'system':
            continue
        parts.append(f"{role.upper()}: {_content(message.get('content', ''))}")
        meta = _metadata(message.get('metadata'))
        if meta.get('evidence') or meta.get('sources'):
            parts.append('SOURCES: ' + json.dumps({
                'evidence': meta.get('evidence', []),
                'sources': meta.get('sources', []),
            }, ensure_ascii=False))
    return '\n\n'.join(parts)


def summarize(messages: list[dict], previous: dict | None = None) -> dict:
    if not messages and previous:
        return previous
    result = chat_json(
        _render(messages, previous), system=SUMMARY_SYSTEM,
        schema=SUMMARY_SCHEMA, max_tokens=4096,
    )
    return {
        'summary': str(result.get('summary') or '').strip(),
        'facts': [str(x) for x in result.get('facts', []) if str(x).strip()],
        'decisions': [str(x) for x in result.get('decisions', []) if str(x).strip()],
        'openThreads': [
            str(x) for x in result.get('openThreads', []) if str(x).strip()
        ],
        'sources': [
            {'title': str(x.get('title') or ''), 'url': str(x.get('url') or '')}
            for x in result.get('sources', []) if isinstance(x, dict) and x.get('url')
        ],
    }


def format_summary(data: dict, heading: str) -> str:
    lines = [heading, data.get('summary', '').strip()]
    for label, key in (
        ('Facts', 'facts'), ('Decisions', 'decisions'),
        ('Open threads', 'openThreads'),
    ):
        values = data.get(key) or []
        if values:
            lines += ['', f'{label}:', *[f'- {v}' for v in values]]
    sources = data.get('sources') or []
    if sources:
        lines += ['', 'Sources:', *[
            f"- {s.get('title') or s.get('url')}: {s.get('url')}" for s in sources
        ]]
    return '\n'.join(x for x in lines if x is not None)[:MAX_HANDOFF_CHARS]


def _load_content(row) -> dict | None:
    if not row or row['status'] != 'done' or not row['content']:
        return None
    try:
        data = json.loads(row['content'])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def handoff_context(conversation_id: str | None) -> str:
    if not conversation_id:
        return ''
    rows = get_db().execute(
        "SELECT metadata FROM messages WHERE conversation_id=? AND role='system' "
        'ORDER BY created_at DESC, id DESC',
        (conversation_id,),
    ).fetchall()
    for row in rows:
        meta = _metadata(row['metadata'])
        if not meta.get('break'):
            continue
        if not meta.get('carryContext', True):
            return ''
        compaction_id = meta.get('compactionId')
        compaction = get_db().execute(
            'SELECT * FROM chat_compactions WHERE id=?', (compaction_id,),
        ).fetchone()
        data = _load_content(compaction)
        return format_summary(data, 'Durable context from the previous chat:') if data else ''
    return ''


def evidence_context(conversation_id: str | None) -> str:
    """Recent compact local evidence, excluding anything before the last break."""
    if not conversation_id:
        return ''
    rows = get_db().execute(
        'SELECT role, metadata FROM messages WHERE conversation_id=? '
        'ORDER BY created_at DESC, id DESC',
        (conversation_id,),
    ).fetchall()
    records = []
    size = 0
    for row in rows:
        meta = _metadata(row['metadata'])
        if row['role'] == 'system' and meta.get('break'):
            break
        for item in reversed(meta.get('evidence') or []):
            if not isinstance(item, dict):
                continue
            rendered = json.dumps(item, ensure_ascii=False)
            if size + len(rendered) > MAX_EVIDENCE_CHARS:
                continue
            size += len(rendered)
            records.append(item)
    if not records:
        return ''
    records.reverse()
    return (
        'Offline articles consulted in this chat. These are bounded excerpts; '
        'reopen an article with local_knowledge_read when more text is needed:\n\n'
        + json.dumps(records, ensure_ascii=False)
    )


def _latest_rolling(conversation_id: str):
    return get_db().execute(
        "SELECT * FROM chat_compactions WHERE conversation_id=? AND kind='rolling' "
        "AND status='done' ORDER BY created_at DESC, id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()


def compact_for_prompt(messages: list[dict], conversation_id: str | None):
    """Return (messages, compacted-context), creating a rolling summary if needed."""
    if not conversation_id:
        return messages, ''
    current = list(messages)
    prior_row = _latest_rolling(conversation_id)
    prior = _load_content(prior_row)
    covered: list[str] = []
    if prior_row:
        try:
            covered = json.loads(prior_row['source_message_ids'])
        except json.JSONDecodeError:
            covered = []
    current_ids = {m.get('id') for m in current if m.get('id')}
    # The newest rolling row may belong to the segment before a New Chat
    # marker. The browser sends only the current segment, so no shared message
    # id means this summary is intentionally out of scope (especially for a
    # clean slate).
    if not current_ids.intersection(covered):
        prior_row = None
        prior = None
        covered = []
    covered_set = set(covered)
    remaining = [m for m in current if m.get('id') not in covered_set]
    context = format_summary(prior, 'Earlier in this chat:') if prior else ''
    effective = ([{'role': 'system', 'content': context}] if context else []) + remaining
    if estimate_tokens(effective) <= SOFT_LIMIT_TOKENS:
        return remaining, context

    recent_tokens = 0
    cut = len(remaining)
    for i in range(len(remaining) - 1, -1, -1):
        cost = estimate_tokens([remaining[i]])
        if recent_tokens + cost > RECENT_TARGET_TOKENS:
            cut = i + 1
            break
        recent_tokens += cost
        cut = i
    older = remaining[:cut]
    if not older:
        return remaining, context

    try:
        data = summarize(older, prior)
    except Exception as exc:
        if estimate_tokens(effective) >= HARD_LIMIT_TOKENS:
            raise RuntimeError(
                'This chat is too large to continue safely and compaction failed.'
            ) from exc
        logger.warning('Rolling chat compaction failed; using full context: %s', exc)
        return remaining, context
    source_ids = covered + [m['id'] for m in older if m.get('id')]
    now = int(time.time())
    db = get_db()
    db.execute(
        "INSERT INTO chat_compactions(id, conversation_id, kind, source_message_ids, "
        "content, status, carry_context, created_at, updated_at) "
        "VALUES (?,?,'rolling',?,?,'done',1,?,?)",
        (str(ULID()), conversation_id, json.dumps(source_ids),
         json.dumps(data), now, now),
    )
    db.commit()
    return remaining[cut:], format_summary(data, 'Earlier in this chat:')


def _segment_rows(conversation_id: str) -> list[dict]:
    rows = get_db().execute(
        'SELECT id, role, content, metadata, created_at FROM messages '
        'WHERE conversation_id=? ORDER BY created_at, id',
        (conversation_id,),
    ).fetchall()
    segment: list[dict] = []
    for row in rows:
        item = dict(row)
        if item['role'] == 'system' and _metadata(item['metadata']).get('break'):
            segment = []
        elif item['role'] in ('user', 'assistant'):
            segment.append(item)
    return segment


def create_break(conversation_id: str, *, carry_context: bool = True) -> dict:
    """Persist the boundary first, then queue its retryable compaction."""
    db = get_db()
    segment = _segment_rows(conversation_id)
    now = int(time.time())
    compaction_id = str(ULID())
    break_id = str(ULID())
    status = 'pending' if carry_context and segment else 'done'
    metadata = json.dumps({
        'break': True, 'carryContext': carry_context,
        'compactionId': compaction_id,
    })
    db.execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata, status, created_at) "
        "VALUES (?,?,'system','',?,?,?)",
        (break_id, conversation_id, metadata,
         'streaming' if status == 'pending' else 'done', now),
    )
    db.execute(
        "INSERT INTO chat_compactions(id, conversation_id, kind, source_message_ids, "
        "content, status, carry_context, break_message_id, created_at, updated_at) "
        "VALUES (?,?,'break',?,?,?,?,?,?,?)",
        (compaction_id, conversation_id, json.dumps([m['id'] for m in segment]),
         json.dumps({}) if status == 'done' else None, status,
         int(carry_context), break_id, now, now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?',
               (now, conversation_id))
    db.commit()
    if status == 'pending':
        from backend.ai import jobs
        jobs.enqueue('chat.compaction', compaction_id)
    return {'id': break_id, 'compactionId': compaction_id, 'status': status}


def run_pending(compaction_id: str) -> None:
    db = get_db()
    row = db.execute('SELECT * FROM chat_compactions WHERE id=?',
                     (compaction_id,)).fetchone()
    if not row or row['status'] != 'pending':
        return
    try:
        ids = json.loads(row['source_message_ids'])
        previous = None
        rolling = _latest_rolling(row['conversation_id'])
        rolling_ids: list[str] = []
        if rolling:
            try:
                rolling_ids = json.loads(rolling['source_message_ids'])
            except json.JSONDecodeError:
                rolling_ids = []
            if set(rolling_ids).issubset(set(ids)):
                previous = _load_content(rolling)
            else:
                rolling_ids = []
        rolling_id_set = set(rolling_ids)
        unread_ids = [message_id for message_id in ids
                      if message_id not in rolling_id_set]
        placeholders = ','.join('?' for _ in unread_ids)
        messages = [] if not unread_ids else [dict(r) for r in db.execute(
            f'SELECT id, role, content, metadata FROM messages WHERE id IN ({placeholders}) '
            'ORDER BY created_at, id', unread_ids,
        ).fetchall()]
        data = summarize(messages, previous)
        now = int(time.time())
        db.execute(
            "UPDATE chat_compactions SET content=?, status='done', error=NULL, updated_at=? "
            'WHERE id=?', (json.dumps(data), now, compaction_id),
        )
        if row['break_message_id']:
            db.execute("UPDATE messages SET status='done', error=NULL WHERE id=?",
                       (row['break_message_id'],))
        db.commit()
    except Exception as exc:
        logger.warning('Chat compaction %s failed: %s', compaction_id, exc)
        now = int(time.time())
        db.execute(
            "UPDATE chat_compactions SET status='error', error=?, updated_at=? WHERE id=?",
            (str(exc), now, compaction_id),
        )
        if row['break_message_id']:
            db.execute("UPDATE messages SET status='error', error=? WHERE id=?",
                       (str(exc), row['break_message_id']))
        db.commit()


def recover_pending() -> None:
    """Resume interrupted summaries and retry failures once per app start."""
    db = get_db()
    rows = db.execute(
        "SELECT id, break_message_id, status FROM chat_compactions "
        "WHERE kind='break' AND status IN ('pending','error')"
    ).fetchall()
    if not rows:
        return
    for row in rows:
        if row['status'] == 'error':
            db.execute(
                "UPDATE chat_compactions SET status='pending', error=NULL WHERE id=?",
                (row['id'],),
            )
            if row['break_message_id']:
                db.execute(
                    "UPDATE messages SET status='streaming', error=NULL WHERE id=?",
                    (row['break_message_id'],),
                )
    db.commit()
    from backend.ai import jobs
    for row in rows:
        jobs.enqueue('chat.compaction', row['id'])
