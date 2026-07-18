from backend.ai.provider import get_provider_config, get_ollama_client, is_ai_configured, DEFAULT_MODELS

_MAX_INPUT_CHARS = 48000

_SUMMARY_SYSTEM = (
    "You summarize a meeting transcript. Speaker labels: \"Me\" is the user; "
    "\"Speaker N\" / \"Others\" are the other participants.\n"
    "Write clean markdown with these sections (omit a section if empty):\n"
    "## Overview — 2-4 sentences on what the meeting was about\n"
    "## Key points — bullet list of decisions and important information\n"
    "## Action items — bullet list, note who owns each when clear\n"
)


def summarize_meeting(transcript: str) -> str | None:
    """Summarize a meeting transcript into markdown, or None when AI is
    unconfigured or summarization fails (not an error for the pipeline)."""
    if not transcript.strip():
        return None
    # Keep the tail: late discussion carries the conclusions.
    transcript = transcript[-_MAX_INPUT_CHARS:]
    try:
        if not is_ai_configured():
            return None
        c = get_provider_config()
        messages = [
            {'role': 'system', 'content': _SUMMARY_SYSTEM},
            {'role': 'user', 'content': transcript},
        ]
        client = get_ollama_client(c)
        model = c['ollama_model'] or DEFAULT_MODELS['ollama']
        resp = client.chat.completions.create(model=model, messages=messages, stream=False)
        text = resp.choices[0].message.content
        text = (text or '').strip()
        return text or None
    except Exception as e:
        print(f'Meeting summarization failed: {e}')

    return None
