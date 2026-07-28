import subprocess
from pathlib import Path

from backend.storage import IdScopedStorage

_storage = IdScopedStorage('MEETINGS_ROOT', './data/meetings')

meetings_root = _storage.root
meeting_dir = _storage.dir
delete_meeting_dir = _storage.delete_dir


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
