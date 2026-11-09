"""One journal entry, described whole, for readers that cannot see it.

The briefing used to read an entry as its title and body. For an entry that is
mostly attachments — a run of screenshots, a photo with one line under it, a
recording made with the bottom bar's Record button — that is a title over an
empty body: every caption and transcript the pipeline had already produced sat
on the attachments, where the briefing never looked. This writes one account of
the entry from all of it together, so the attachments are read in the light of
each other and of what the user wrote.

Prompt-building is pure so it can be tested without a model; only
`describe_entry` makes the call. The nightly pass that drives it lives in
backend/journal/digest_job.py.
"""
from datetime import datetime

from backend.ai.llm import chat_json

# Room for the answer. A thinking budget, when the overnight switch is on, is
# added on top of this by `_request_kwargs`.
DIGEST_MAX_TOKENS = 1200

# How much of any one input reaches the prompt. The context window is large,
# but prompt evaluation on this machine is not free, and a 40-minute transcript
# adds little to a description that is a paragraph long.
BODY_MAX_CHARS = 12000
MEDIA_TEXT_MAX_CHARS = 4000

SYSTEM_PROMPT = """You are writing the description of one journal entry for an \
assistant that cannot see photos, screenshots or recordings. Your description \
replaces the entry when that assistant reads it, so everything worth knowing \
about the entry has to be in it.

You are given what the user wrote (if anything) and a machine reading of every \
attachment: a caption of each photo, a transcription of each screenshot, what \
was said in each recording or video, and descriptions of their sounds.

Write one plain paragraph, or two if the entry is long, that:
- keeps what the user wrote — their own words matter most, so quote short \
phrases and closely paraphrase the rest;
- says what the attachments show and contain, specifically: the app or site, \
the names, titles, prices, places and key sentences, quoted where they matter;
- treats a run of screenshots as one activity — what they were reading, \
comparing, buying or talking about — rather than listing them one by one;
- connects the attachments to the text where the connection is plain.

Be factual. Do not guess at how the user feels or why they saved something \
unless they said so. Write about the entry in the third person ("They saved \
…"), never addressing the user. If an attachment was not read (no caption), \
say only that it is there."""

SCHEMA = {
    'type': 'object',
    'properties': {'description': {'type': 'string'}},
    'required': ['description'],
    'additionalProperties': False,
}


def _clip(text: str | None, limit: int) -> str:
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit].rstrip() + ' […]'


def _when(ts: int | None) -> str:
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else 'unknown time'


def _attachment_lines(a: dict) -> list[str]:
    """What is known about one attachment, labelled by what produced it."""
    kind = a['kind']
    at = _when(a.get('captured_at') or a.get('created_at'))
    transcript = _clip(a.get('transcript'), MEDIA_TEXT_MAX_CHARS)
    description = _clip(a.get('description'), MEDIA_TEXT_MAX_CHARS)
    if kind == 'image':
        label = 'Screenshot' if a.get('is_screenshot') else f'Photo "{a["name"]}"'
        reading = transcript or '(not read)'
        return [f'{label}, {at}: {reading}']
    if kind == 'youtube':
        lines = [f'YouTube video "{a["name"]}"']
        if description:
            lines.append(f'  Summary: {description}')
        elif transcript:
            lines.append(f'  What was said: {transcript}')
        return lines
    if kind == 'webpage':
        lines = [f'Web page "{a["name"]}"']
        if transcript:
            lines.append(f'  What it says: {transcript}')
        return lines
    if kind in ('audio', 'video'):
        lines = [f'{"Recording" if kind == "audio" else "Video"} "{a["name"]}", {at}']
        if transcript:
            lines.append(f'  What was said: {transcript}')
        if description:
            lines.append(f'  Sounds: {description}')
        if not transcript and not description:
            lines.append('  (not transcribed)')
        return lines
    return [f'File "{a["name"]}"']


def build_prompt(entry: dict) -> str:
    """Render one entry's inputs (see digest_job.entry_inputs) as the prompt."""
    parts = [f'Entry written {_when(entry.get("created_at"))}.']
    if entry.get('title'):
        parts.append(f'Title: {entry["title"]}')
    body = _clip(entry.get('body'), BODY_MAX_CHARS)
    parts.append('What they wrote:\n' + (body or '(nothing — the entry is only its attachments)'))
    attachments = entry.get('attachments') or []
    if attachments:
        lines = []
        for a in attachments:
            lines += _attachment_lines(a)
        parts.append(f'Attachments ({len(attachments)}, in order):\n' + '\n'.join(lines))
    parts.append('Write the description.')
    return '\n\n'.join(parts)


def describe_entry(entry: dict) -> str | None:
    """The description, or None when the model returned nothing usable.

    Raises whatever the model call raises — the pass tells a paused or
    preempted call apart from a failed one, and only it knows what to do next.
    Thinking comes from the nightly thread's scope (backend/briefing_scheduler.py).
    """
    result = chat_json(
        build_prompt(entry), system=SYSTEM_PROMPT, schema=SCHEMA,
        max_tokens=DIGEST_MAX_TOKENS,
    )
    text = ((result or {}).get('description') or '').strip()
    return text or None
