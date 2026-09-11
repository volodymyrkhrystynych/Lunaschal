"""A short description of what a watched video actually said.

This is the half of a YouTube journal entry the user did not write. The entry's
own text is their commentary; this is the thing being commented on, condensed
enough to make the entry readable months later without opening the video.

It is stored on `journal_attachments.description`, beside the full transcript in
`transcript` — the same split the audio attachments already use, where the
transcript is the record and the description is the summary of it.
"""
from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.ai.service import InferencePaused, Preempted

# Matches backend/ai/meetings.py. Tail rather than head: a talk states its
# conclusions at the end, and the opening two minutes of a YouTube video are
# reliably the least informative part of it.
_MAX_INPUT_CHARS = 48000

_SYSTEM = (
    'You summarize the transcript of a video somebody has just watched.\n'
    'Write 2-4 sentences of plain prose describing what the video said: its '
    'subject, the argument or information it actually delivered, and any '
    'conclusion it reached.\n'
    'Write about the content, not the video as an object — no "this video '
    'explains", no "the speaker discusses", just the substance.\n'
    'The transcript may be auto-generated and so may contain mistranscribed '
    'words; read through them rather than quoting them.\n'
    'Do not add anything that is not in the transcript.'
)

_SCHEMA = {
    'type': 'object',
    'properties': {'summary': {'type': 'string'}},
    'required': ['summary'],
}


def summarize_video(title: str, transcript: str) -> str | None:
    """A few sentences on what the video said, or None.

    None when AI is unconfigured, the transcript is empty, or the call fails —
    a missing summary is not a broken attachment, and the card simply shows the
    video without one. `InferencePaused` and `Preempted` are the exception and
    are re-raised: those mean "run this again later", and swallowing them here
    is how an evening of paused summaries gets recorded as permanent failure.
    """
    if not transcript.strip():
        return None
    transcript = transcript[-_MAX_INPUT_CHARS:]
    prompt = transcript
    if title.strip():
        prompt = f'Video title: {title.strip()}\n\nTranscript:\n{transcript}'
    try:
        if not is_ai_configured():
            return None
        data = chat_json(prompt, system=_SYSTEM, schema=_SCHEMA)
        text = (data.get('summary') or '').strip() if data else ''
        return text or None
    except (InferencePaused, Preempted):
        raise
    except Exception as e:
        print(f'Video summarization failed: {e}')
    return None
