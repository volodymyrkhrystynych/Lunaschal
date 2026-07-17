import os
import re
import shutil
import subprocess
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


def transcode_to_system_track(src: Path, dest: Path) -> float:
    """Transcode an arbitrary uploaded audio file to the 16kHz mono WAV format
    used for recorded meeting tracks, and return its duration in seconds.

    Raises subprocess.CalledProcessError if ffmpeg can't decode the file."""
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(src),
         '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(dest)],
        check=True, capture_output=True,
    )
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(dest)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


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
