"""Fixing the words speech-to-text got wrong, before the message is sent.

Chat dictation used to be submitted verbatim and instantly, so a mangled proper
noun became the message. This is the pass that repairs it, and it only works
because there are two things to check the audio against that the STT model never
had:

1. **The standing memory document** (`backend/memory.py`) — the names the user
   has already corrected once. This is the loop that makes `remember` pay for
   itself: a name written down in one conversation is a name transcribed
   correctly in every later one.
2. **What was read out of the attached photo** (`backend/ai/images.py`) — a
   menu, a label or a receipt in frame routinely spells the exact word that was
   misheard.

With neither reference available there is nothing to correct *against*, and the
pass is skipped rather than turned loose on the text: a model asked to "improve"
a transcript with no reference rewrites it, and rewriting is precisely what must
not happen to a record of what someone said.
"""
import logging

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

_MAX_TOKENS = 1024

_SYSTEM = """You are a speech-to-text corrector. You are given a raw transcript \
and reference material naming things the speaker talks about.

Fix only what the transcriber misheard: wrong words, proper nouns, names of \
people, places, dishes, brands and products. Use the reference's spellings.

Hard rules:
- Change words, nothing else. Never reword, never summarise, never expand, never \
tidy the grammar, never add or remove a sentence. Keep the speaker's own voice, \
their filler and their sentence order exactly as they are.
- Only change a word when the reference gives you a specific reason to. If \
nothing in the reference bears on the transcript, return it exactly as given.
- Never answer the transcript, never comment on it, and never add anything that \
was not said.

Return the corrected transcript in the `corrected` field."""

_SCHEMA = {
    'type': 'object',
    'properties': {'corrected': {'type': 'string'}},
    'required': ['corrected'],
}


def correct_transcript(
    text: str,
    *,
    memory: str = '',
    photo_notes: list[str] | None = None,
    image_parts: list[dict] | None = None,
) -> str:
    """The corrected transcript, or `text` unchanged.

    `image_parts` are OpenAI `image_url` parts, passed when the chat model reads
    photos itself — then the corrector looks at the label or menu directly
    instead of at somebody else's paragraph about it, which is the difference
    between fixing a dish name and guessing at one. `photo_notes` is the same
    reference for the text-only path. Both are optional and the memory alone is
    a legitimate reference.

    Never raises and never returns empty. A correction pass that fails must cost
    the user nothing — the transcript they dictated is the thing being protected,
    not the improvement.
    """
    text = (text or '').strip()
    photo_notes = [n for n in (photo_notes or []) if n and n.strip()]
    image_parts = list(image_parts or [])
    if not text or not is_ai_configured():
        return text

    reference = []
    if memory and memory.strip():
        reference.append(f'Things known about the speaker:\n{memory.strip()}')
    for i, note in enumerate(photo_notes, 1):
        reference.append(f'Photo {i} they attached, as read by a vision model:\n{note.strip()}')
    if image_parts:
        reference.append(
            'The photo(s) they attached are included with this message — read any '
            'text visible in them, such as a menu, label or receipt, and trust '
            'those spellings over the transcript.'
        )
    if not reference:
        return text

    prompt = (
        'Reference material:\n---\n' + '\n\n'.join(reference) + '\n---\n\n'
        f'Raw transcript:\n{text}'
    )
    try:
        if image_parts:
            result = _correct_with_images(prompt, image_parts)
        else:
            result = chat_json(prompt, system=_SYSTEM, schema=_SCHEMA,
                               thinking=False, max_tokens=_MAX_TOKENS)
    except Exception as e:
        logger.warning('Transcript correction failed: %s', e)
        return text

    corrected = (result or {}).get('corrected')
    if not isinstance(corrected, str) or not corrected.strip():
        return text
    return corrected.strip()


def _correct_with_images(prompt: str, image_parts: list[dict]) -> dict:
    """The same call as `chat_json`, with the photos attached.

    Built here rather than adding a content-parts argument to `chat_json`: every
    one of that helper's several dozen call sites passes a plain prompt, and this
    is the only place in the app that wants a one-shot JSON answer *about an
    image*. The grammar (`schema=`) still applies — that is the part worth
    keeping, since it is what stops the model answering the transcript instead of
    correcting it.
    """
    from backend.ai.llm import chat_json_messages

    return chat_json_messages(
        [
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': [{'type': 'text', 'text': prompt}] + image_parts},
        ],
        schema=_SCHEMA,
        max_tokens=_MAX_TOKENS,
    )
