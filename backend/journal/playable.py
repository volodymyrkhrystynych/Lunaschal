"""An AAC copy of a voice clip, for the one client that cannot play the original.

A clip recorded in the desktop window or Chrome is WebM/Opus, and AVFoundation
— so the native iPhone app — cannot open WebM at all. Rather than send the
phone a file it can only report as broken, `GET /api/journal/attachments/<id>/
file?playable=1` hands it this copy instead.

The copy is made on first request and kept beside the original, in the
attachment's own directory, so deleting the attachment deletes it too and a
second listen costs nothing. It is rebuilt when the original is newer. Video is
left alone: a browser-made WebM video is rare, and re-encoding one is minutes of
CPU on a request somebody is waiting on.
"""
import os
import subprocess
from pathlib import Path

# What AVFoundation opens directly. Everything else in storage.AUDIO_EXTS
# (webm, ogg, oga, opus) needs the copy.
APPLE_AUDIO_EXTS = {'m4a', 'mp3', 'wav', 'aac', 'mp4', 'flac'}

COPY_NAME = 'phone.m4a'


class PlayableUnavailable(Exception):
    """ffmpeg is missing or could not read the original."""


def needs_copy(path: Path, kind: str) -> bool:
    return kind == 'audio' and path.suffix.lower().lstrip('.') not in APPLE_AUDIO_EXTS


def aac_copy(path: Path) -> Path:
    """The AAC copy of `path`, made now if it is missing or older than it."""
    dest = path.parent / COPY_NAME
    if dest.is_file() and dest.stat().st_mtime >= path.stat().st_mtime:
        return dest
    # Written under a temporary name and renamed, so a request that arrives
    # while another is still encoding never serves half a file.
    partial = path.parent / f'.{COPY_NAME}.{os.getpid()}.part'
    try:
        subprocess.run(
            ['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', str(path),
             '-vn', '-c:a', 'aac', '-b:a', '96k', '-f', 'ipod', str(partial)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=600,
        )
        os.replace(partial, dest)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        partial.unlink(missing_ok=True)
        raise PlayableUnavailable('Could not convert this clip') from e
    return dest
