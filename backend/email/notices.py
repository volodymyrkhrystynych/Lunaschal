"""The "this mail looks important" chat messages, as a model may see them.

Their text is built from what strangers wrote, and they sit in the chat where
every later turn replays them — turns that start with the web and the writing
tools open, because nothing called an email tool. So wherever a stored message
becomes model input its text is replaced by a stub of our own facts (how many,
which closed-enum reason, our own ids). The user's copy is never touched. A
model that needs the content calls list_emails or read_email, and that is what
arms the guard (backend/email/tools.py).
"""
import json
import re

from backend.db.connection import get_db

REASONS = {'job_next_step': 'Job next step', 'personal': 'Personal',
           'security': 'Security', 'deadline': 'Deadline'}
_OUR_ID = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}')
_CHUNK = 500


def _notice(metadata) -> dict | None:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    email = metadata.get('email') if isinstance(metadata, dict) else None
    return email if isinstance(email, dict) and email.get('kind') == 'important' else None


def stub(metadata) -> str | None:
    """What a model sees in place of an important-mail message, or None when
    the message isn't one."""
    notice = _notice(metadata)
    if notice is None:
        return None
    items = [i for i in notice.get('items') or [] if isinstance(i, dict)]
    parts = []
    for item in items:
        bits = [x for x in (REASONS.get(str(item.get('reason'))),
                            item['id'] if _OUR_ID.fullmatch(str(item.get('id'))) else None) if x]
        if bits:
            parts.append(' '.join(bits))
    return (f'[Notice of {len(items) or 1} important email(s)'
            + (f': {"; ".join(parts)}' if parts else '')
            + '. Text withheld here: call read_email or list_emails if they ask about it.]')


def _flat(content) -> str:
    return ' '.join(content.split()) if isinstance(content, str) else ''


def neutralize(messages: list[dict]) -> list[dict]:
    """`messages` with every important-mail notice replaced by its stub.

    Decided by the stored row, not by what came with the message: a client
    sends its own history and need not send metadata, or an id. A message whose
    id does not lead to that same stored text is matched on its text instead.
    """
    db = get_db()
    ids = list({m['id'] for m in messages if isinstance(m.get('id'), str)})
    stored = {}
    for at in range(0, len(ids), _CHUNK):
        chunk = ids[at:at + _CHUNK]
        stored.update((r['id'], r) for r in db.execute(
            f"SELECT id, content, metadata FROM messages WHERE id IN ({','.join('?' * len(chunk))})", chunk))

    by_text: dict[str, str] | None = None
    out = []
    for m in messages:
        row = stored.get(m['id']) if isinstance(m.get('id'), str) else None
        text = (stub(row['metadata']) if row else None) or stub(m.get('metadata'))
        if text is None and m.get('role') == 'assistant' and (row is None or row['content'] != m.get('content')):
            if by_text is None:
                by_text = {_flat(r['content']): stub(r['metadata']) for r in db.execute(
                    "SELECT content, metadata FROM messages WHERE role='assistant' "
                    "AND metadata LIKE '%\"important\"%'") if _notice(r['metadata'])}
            text = by_text.get(_flat(m.get('content')))
        out.append({**m, 'content': text} if text else m)
    return out
