"""Getting a message's attached photos in front of the chat model.

There are two ways to do this and the app supports both, chosen by the
`llama_chat_vision` setting:

**Directly.** Qwen3.6 35B A3B is a vision-language model — its GGUF carries
mRoPE's four rope sections and the `image-text-to-text` tag — so with an `mmproj`
on `[qwen36]` the photo can ride into the turn as an OpenAI `image_url` content
part and the model looks at it with the conversation in hand. This is strictly
better where it works: the decision turn can read a nutrition label to answer the
question actually being asked, and a follow-up about the picture is answerable at
all.

**Read to it.** Without a projector the chat model is text-only, so
`backend/ai/images.read_chat_photo` has the CPU-resident `[gemma4-12b-omni]`
describe the photo first and that description goes in as text. Lossy by
construction — the description is written before anyone knows what will be asked
of it — but it needs no VRAM on the chat preset and no extra download.

The fallback is not vestigial: `[qwen36]` ships without an `mmproj` because there
is only ~878 MiB of VRAM headroom at 190k context, so "read to it" is what runs
until someone has confirmed a projector loads.

Assembly happens here, on the server, rather than in the browser: the client
sends attachment ids and nothing else, so what the model is told about a photo
stays a backend decision. Either way this is the one seam — nothing else in the
chat path knows a photo exists.
"""
import logging

from backend.ai.provider import chat_vision_enabled
from backend.db.connection import get_db

logger = logging.getLogger(__name__)

# Said out loud in the prompt because the model will otherwise talk as though it
# looked at the picture itself, and the user needs to know it didn't.
_PREFIX = (
    'Photo attached. You cannot see images; this is what a separate vision model '
    'read from it'
)

_UNREAD = (
    '[Photo attached, but it could not be read — you do not know what is in it. '
    'Say so if it matters rather than guessing.]'
)

_PENDING = (
    '[Photo attached, but it has not finished being read yet — you do not know '
    'what is in it. Say so if it matters rather than guessing.]'
)

_MISSING_FILE = (
    '[Photo attached, but its file is missing — you do not know what is in it.]'
)

_ATTACHMENT_COLS = 'id, path, description, description_status'


def _rows_for(attachment_ids: list[str]):
    """The attachment rows for these ids, in the order given.

    Unknown ids are skipped silently — a client replaying a stale message must
    not be able to make a turn fail.
    """
    ids = [a for a in attachment_ids if isinstance(a, str) and a]
    if not ids:
        return []
    placeholders = ','.join('?' * len(ids))
    rows = get_db().execute(
        f'SELECT {_ATTACHMENT_COLS} FROM chat_attachments WHERE id IN ({placeholders})',
        ids,
    ).fetchall()
    by_id = {r['id']: r for r in rows}
    return [by_id[a] for a in ids if a in by_id]


def _block(row) -> str:
    description = (row['description'] or '').strip()
    if description:
        return f'[{_PREFIX}: {description}]'
    if row['description_status'] == 'running':
        return _PENDING
    return _UNREAD


def descriptions_for(attachment_ids: list[str]) -> list[str]:
    """The text blocks describing these attachments, for the text-only path."""
    return [_block(row) for row in _rows_for(attachment_ids)]


def image_parts_for(attachment_ids: list[str]) -> tuple[list[dict], list[str]]:
    """`image_url` content parts for these attachments, plus notes about any that
    could not be turned into one.

    A file that is missing or in a format the projector can't decode becomes a
    text note rather than an exception: one bad attachment must not cost the turn
    the message it was attached to.
    """
    from backend.ai.images import VisionUnavailable, data_uri
    from backend.chat import storage

    parts: list[dict] = []
    notes: list[str] = []
    for row in _rows_for(attachment_ids):
        path = storage.resolve_stored_path(row['path'])
        if path is None or not path.is_file():
            notes.append(_MISSING_FILE)
            continue
        try:
            parts.append({'type': 'image_url', 'image_url': {'url': data_uri(path)}})
        except VisionUnavailable as e:
            logger.warning('Chat photo %s cannot be sent to the model: %s', row['id'], e)
            notes.append(f'[Photo attached, but it could not be decoded: {e}.]')
    return parts, notes


def expand_attachments(messages: list[dict]) -> list[dict]:
    """Put each message's photos in front of the model.

    With chat vision on the message becomes a list of OpenAI content parts; with
    it off the photos' readings are appended to the text. Messages carrying no
    `attachmentIds` pass through untouched either way, which is what keeps the
    voice listener, task nudges and Writing discussions — none of which can
    attach anything — on exactly the path they had before.
    """
    vision = chat_vision_enabled()
    out = []
    for m in messages:
        ids = m.get('attachmentIds') or []
        if not ids:
            out.append(m)
            continue
        text = (m.get('content') or '').strip()

        if not vision:
            blocks = descriptions_for(ids)
            if not blocks:
                out.append(m)
                continue
            joined = '\n'.join(blocks)
            out.append({**m, 'content': f'{text}\n\n{joined}' if text else joined})
            continue

        parts, notes = image_parts_for(ids)
        if not parts and not notes:
            out.append(m)
            continue
        # The text part goes first: it is what `stamp_messages` will prefix with
        # the timestamp, and it reads as the caption to the images rather than an
        # afterthought below them.
        leading = '\n\n'.join([t for t in [text, '\n'.join(notes)] if t])
        content: list[dict] = [{'type': 'text', 'text': leading}] if leading else []
        out.append({**m, 'content': content + parts})
    return out
