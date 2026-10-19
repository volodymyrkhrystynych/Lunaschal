"""One line of reading per server oddity — the only part of the oddities pass
that touches the model.

Finding and counting the errors is deterministic (backend/ops/oddities.py);
this only says what each one probably means. It gets every item in one call
rather than one call each, because problems with one cause usually show up as
several fingerprints (a CPU model that is down fails audio description at three
different call sites), and a model looking at them side by side can say so.
"""
import json
from pathlib import Path

from backend.ai.llm import chat_json

MAX_ITEMS = 8
NOTE_MAX = 320
SNIPPET_RADIUS = 6

# The app's own checkout, for reading the frame a traceback points at. A frame's
# file is repo-relative (backend/ops/oddities.py), so this works from any copy.
REPO_ROOT = Path(__file__).resolve().parents[2]

SYSTEM_PROMPT = (
    "You review the error log of a single-user home server for the person who "
    "wrote it and runs it. The server is Lunaschal: a Flask app on Python, with "
    "local models behind llama-server's router on :8080 — the `qwen36` chat model "
    "on the GPU, and `gemma4-12b-omni` (photos, audio description) and `embed` "
    "on the CPU. Background model work is queued in an `llm_jobs` table.\n\n"
    "Each item below is one *kind* of problem: a normalised message, how often "
    "it happened, and a real sample. For each, write one or two plain sentences: "
    "the likely cause, and whether it looks harmless, needs fixing, or is a "
    "symptom of something bigger. When several items share one cause, say so on "
    "each of them. Base it only on what is shown; if the cause is unclear, say "
    "that rather than guessing. No preamble, no markdown headings."
)


def _snippet(frame: dict | None) -> str | None:
    """The lines around the frame a traceback ends in, from our own source."""
    if not frame or not frame.get('file'):
        return None
    path = (REPO_ROOT / frame['file']).resolve()
    if REPO_ROOT not in path.parents or not path.is_file():
        return None
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    line = int(frame.get('line') or 0)
    if not 1 <= line <= len(lines):
        return None
    lo, hi = max(1, line - SNIPPET_RADIUS), min(len(lines), line + SNIPPET_RADIUS)
    return '\n'.join(
        f"{'>' if n == line else ' '}{n:5d}  {lines[n - 1]}" for n in range(lo, hi + 1)
    )


def build_prompt(items: list[dict]) -> str:
    """Pure: render the items for the model."""
    parts = []
    for item in items:
        lines = [
            f"### {item['fingerprint']}",
            f"unit: {item['unit']} · kind: {item['kind']} · status: {item['status']}",
            f"seen {item['count']}× in the last scan, on {item['days']} of the last 7 days",
            f"message: {item['template']}",
            'sample:',
            item['sample'],
        ]
        snippet = _snippet(item.get('frame'))
        if snippet:
            lines += [f"source around {item['frame']['file']}:{item['frame']['line']}:", snippet]
        parts.append('\n'.join(lines))
    return '\n\n'.join(parts) + '\n\nWrite a note for every item, keyed by its id.'


def annotate(items: list[dict]) -> dict[str, str]:
    """{fingerprint: note} for up to MAX_ITEMS items. Raises whatever the model
    call raises; the caller decides that a missing note is not a failure."""
    items = items[:MAX_ITEMS]
    if not items:
        return {}
    ids = [item['fingerprint'] for item in items]
    schema = {
        'type': 'object',
        'properties': {
            'notes': {
                'type': 'array',
                'maxItems': len(ids),
                'items': {
                    'type': 'object',
                    'properties': {
                        'id': {'type': 'string', 'enum': ids},
                        'note': {'type': 'string'},
                    },
                    'required': ['id', 'note'],
                    'additionalProperties': False,
                },
            },
        },
        'required': ['notes'],
        'additionalProperties': False,
    }
    result = chat_json(build_prompt(items), system=SYSTEM_PROMPT, schema=schema)
    notes = {}
    for entry in (result or {}).get('notes') or []:
        if not isinstance(entry, dict):
            continue
        fp, note = entry.get('id'), (entry.get('note') or '').strip()
        if fp in ids and note and fp not in notes:
            notes[fp] = ' '.join(note.split())[:NOTE_MAX]
    return notes


def frame_from_json(raw: str | None) -> dict | None:
    try:
        frame = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return frame if isinstance(frame, dict) else None
