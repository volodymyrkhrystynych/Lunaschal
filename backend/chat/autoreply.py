"""Starting a chat reply with nobody on the other end of a connection.

`POST /api/chat/stream` is the browser asking for a reply and handing over the
transcript it already has on screen. A dictated message has neither: the phone
is in a pocket by the time the clip finishes transcribing, and the words that
make up the question were written by a background job rather than by a composer.

So this rebuilds what the browser would have sent, from the rows, and starts the
same background run the streaming endpoint does. Three pieces already existed and
are the reason this is short rather than a second chat implementation:

- `compaction._segment_rows` computes the current segment server-side — the same
  thing `src/lib/chatSegments.ts`'s `contextMessages` computes in the browser,
  and the only reason a second copy of the break rule is not needed here.
- `delegate_chat.stream_reply` builds the entire system prompt itself when
  handed `''`. The browser has never supplied one for the Chat tab.
- `runs.start` owns the assistant row and finishes it regardless of who is
  listening, which is what made the *back* half of a turn survive a walk-away
  long before the front half did.

Priority takes care of itself. `backend/ai/service.py` keys priority to the
thread (`threading.local`), and `runs.start` spawns a fresh one — so a reply
started from inside a P2 transcription job is still P1, exactly as it is when a
browser asks for it. There is nothing to thread through, and nothing to undo.
"""
import logging
import time

from ulid import ULID

from backend.ai.provider import is_ai_configured
from backend.chat.compaction import _segment_rows
from backend.db.connection import get_db, mapping_to_dict
from backend.delegate import runs

logger = logging.getLogger(__name__)


def segment_messages(conversation_id: str) -> list[dict]:
    """The current segment, shaped the way the browser sends it.

    `mapping_to_dict` is what turns `created_at` into the ISO string
    `stamp_messages` parses — without it every turn would reach the model
    unstamped, and the model would be blind to the gaps in a conversation held
    across a whole day.
    """
    rows = _segment_rows(conversation_id)
    if not rows:
        return []

    db = get_db()
    placeholders = ','.join('?' * len(rows))
    attachments = db.execute(
        f'SELECT id, message_id FROM chat_attachments'
        f' WHERE message_id IN ({placeholders}) ORDER BY position, created_at',
        [r['id'] for r in rows],
    ).fetchall()
    by_message: dict[str, list[str]] = {}
    for a in attachments:
        by_message.setdefault(a['message_id'], []).append(a['id'])

    out = []
    for row in rows:
        item = mapping_to_dict(row)
        item['attachmentIds'] = by_message.get(row['id'], [])
        out.append(item)
    return out


def _already_answered(db, conversation_id: str, user_message_id: str) -> bool:
    """Whether this question has a reply, or one already on its way.

    Both halves matter and they catch different things. A later assistant row
    means the answer exists — the clip was replayed after the turn had already
    happened. A row still `'streaming'` anywhere in the conversation means one is
    being written right now, which is what stops two clips landing together from
    starting two runs that would then both answer into the same segment.

    Ordered by id rather than `created_at`: a whole exchange fits inside one
    second, and the ULID is the only tie-break that reflects the real order —
    the same reason `_previous_user_message` compares them in routes/chat.py.
    """
    if db.execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND status='streaming' LIMIT 1",
        (conversation_id,),
    ).fetchone():
        return True
    return db.execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND role='assistant'"
        ' AND id > ? LIMIT 1',
        (conversation_id, user_message_id),
    ).fetchone() is not None


def start_reply(conversation_id: str, user_message_id: str) -> str | None:
    """Answer `user_message_id`, on a background thread. Returns the assistant
    message id, or None if no run was started.

    Every reason to decline is a normal outcome rather than an error: no AI
    configured, the message has nothing in it yet, the segment is empty, or the
    question has already been answered. The caller is a background job whose real
    work — storing the audio and its transcript — is already done and committed.
    """
    if not is_ai_configured():
        logger.info('Not auto-replying to %s: AI is not configured', user_message_id)
        return None

    db = get_db()
    row = db.execute(
        'SELECT conversation_id, content FROM messages WHERE id=?', (user_message_id,)
    ).fetchone()
    if row is None or row['conversation_id'] != conversation_id:
        return None
    # A transcript that came back empty must not become a turn. The model would
    # be asked a question consisting of a timestamp, and would answer it.
    if not (row['content'] or '').strip():
        logger.info('Not auto-replying to %s: it has no text', user_message_id)
        return None
    if _already_answered(db, conversation_id, user_message_id):
        logger.info('Not auto-replying to %s: already answered', user_message_id)
        return None

    messages = segment_messages(conversation_id)
    if not messages:
        return None

    message_id = str(ULID())
    db.execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata,"
        " status, created_at) VALUES (?,?,'assistant','',NULL,'streaming',?)",
        (message_id, conversation_id, int(time.time())),
    )
    db.commit()
    # No queue to relay: nobody asked for this over a connection. The run
    # checkpoints the row as it goes and the Chat tab's poll is what picks it up
    # — the same path a browser takes after its stream drops.
    runs.start(message_id, messages, '', tools_enabled=True,
               conversation_id=conversation_id)
    logger.info('Auto-replying to %s as %s', user_message_id, message_id)
    return message_id
