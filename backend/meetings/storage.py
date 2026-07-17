import os
import re
import shutil
from pathlib import Path

_SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')


def meetings_root() -> Path:
    return Path(os.environ.get('MEETINGS_ROOT', './data/meetings')).expanduser().resolve()


def meeting_dir(meeting_id: str) -> Path | None:
    # Dot-only names like '..' pass _SAFE_NAME but escape the root.
    if not _SAFE_NAME.match(meeting_id) or set(meeting_id) == {'.'}:
        return None
    return meetings_root() / meeting_id


def mic_path(meeting_id: str) -> Path | None:
    d = meeting_dir(meeting_id)
    return d / 'mic.wav' if d else None


def system_path(meeting_id: str) -> Path | None:
    d = meeting_dir(meeting_id)
    return d / 'system.wav' if d else None


def delete_meeting_dir(meeting_id: str) -> None:
    d = meeting_dir(meeting_id)
    if d is None:
        return
    # Belt and braces: only ever delete a direct child of the meetings root.
    d = d.resolve()
    if d.parent != meetings_root():
        return
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
