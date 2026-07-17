"""ffmpeg process management for meeting recordings.

Captures two PulseAudio/PipeWire streams as separate WAV files: the
microphone and the default sink's `.monitor` (the computer's audio output).
Only one meeting can record at a time.

When the `meeting_echo_cancel` setting is on, the mic is captured through
PipeWire's echo-cancel module in `monitor.mode`, which reads its reference
signal straight from the default sink's monitor — no virtual sink is
created and playback is never rerouted, so starting a recording can't ever
silence the speakers. Any failure falls back to the raw mic — echo
cancellation must never cost a recording.
"""
import logging
import signal
import subprocess
import threading
import time
from pathlib import Path

from backend.db.connection import get_db
from backend.meetings import storage

logger = logging.getLogger(__name__)

_rec_lock = threading.Lock()
_active: dict | None = None  # {'meeting_id', 'procs': list[Popen], 'started_mono', 'ec_state'}

EC_SOURCE = 'lunaschal_ec_source'


class RecorderBusy(Exception):
    pass


def _pactl(*args: str) -> str:
    out = subprocess.run(['pactl', *args], capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f'pactl {" ".join(args)} failed')
    return out.stdout.strip()


def _default_sink() -> str:
    try:
        sink = _pactl('get-default-sink')
    except Exception:
        sink = ''
    if not sink:
        raise RuntimeError('Could not determine the default audio sink '
                           '(is pipewire-pulse/pulseaudio running?)')
    return sink


def _echo_cancel_enabled() -> bool:
    try:
        row = get_db().execute('SELECT meeting_echo_cancel FROM settings LIMIT 1').fetchone()
        return bool(row and row['meeting_echo_cancel'])
    except Exception:
        return False


def _cleanup_orphaned_echo_cancel() -> None:
    """Unload any echo-cancel module a crashed/restarted instance left behind
    (identified by our source name in the module arguments)."""
    try:
        for line in _pactl('list', 'short', 'modules').splitlines():
            if 'module-echo-cancel' in line and EC_SOURCE in line:
                _pactl('unload-module', line.split('\t')[0])
    except Exception:
        pass


def _setup_echo_cancel() -> dict | None:
    """Load the echo-cancel module in monitor mode: it reads its AEC
    reference straight from the default sink's monitor instead of creating
    a virtual sink, so playback is never rerouted.

    Returns teardown state, or None when unavailable (caller records raw mic).
    """
    try:
        module_id = _pactl(
            'load-module', 'module-echo-cancel',
            f'source_name={EC_SOURCE}', 'monitor.mode=true', 'aec_method=webrtc',
        )
    except Exception:
        logger.warning('Echo-cancel module failed to load — recording raw mic', exc_info=True)
        return None
    if EC_SOURCE not in _pactl('list', 'short', 'sources'):
        logger.warning('Echo-cancel source did not appear — recording raw mic')
        try:
            _pactl('unload-module', module_id)
        except Exception:
            pass
        return None
    logger.info('Echo cancellation active (module %s, monitor mode)', module_id)
    return {'module_id': module_id}


def _teardown_echo_cancel(state: dict) -> None:
    try:
        _pactl('unload-module', state['module_id'])
    except Exception:
        pass


def _spawn_ffmpeg(pulse_input: str, out_path: Path) -> subprocess.Popen:
    # 16 kHz mono s16le: whisper resamples to that anyway, and it keeps
    # hour-long recordings at ~115 MB per track instead of gigabytes.
    return subprocess.Popen(
        ['ffmpeg', '-y', '-loglevel', 'error',
         '-f', 'pulse', '-i', pulse_input,
         '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
         str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    # SIGINT is ffmpeg's graceful stop — it finalizes the RIFF header.
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def start(meeting_id: str) -> None:
    global _active
    with _rec_lock:
        if _active is not None:
            raise RecorderBusy('A meeting is already being recorded')
        d = storage.meeting_dir(meeting_id)
        if d is None:
            raise RuntimeError('Invalid meeting id')
        d.mkdir(parents=True, exist_ok=True)

        sink = _default_sink()
        mic_input = 'default'
        ec_state = None
        if _echo_cancel_enabled():
            _cleanup_orphaned_echo_cancel()
            ec_state = _setup_echo_cancel()
            if ec_state:
                mic_input = EC_SOURCE
        procs: list[subprocess.Popen] = []
        try:
            procs.append(_spawn_ffmpeg(mic_input, storage.mic_path(meeting_id)))
            procs.append(_spawn_ffmpeg(f'{sink}.monitor', storage.system_path(meeting_id)))
            # Give ffmpeg a beat to fail on a bad pulse device so the start
            # request errors out instead of leaving empty WAVs behind.
            time.sleep(0.3)
            for p in procs:
                if p.poll() is not None:
                    raise RuntimeError('Audio capture failed to start '
                                       '(ffmpeg exited immediately)')
        except Exception:
            for p in procs:
                _stop_proc(p)
            if ec_state:
                _teardown_echo_cancel(ec_state)
            raise
        _active = {'meeting_id': meeting_id, 'procs': procs,
                   'started_mono': time.monotonic(), 'ec_state': ec_state}
        logger.info('Meeting %s: recording started (sink=%s, mic=%s)',
                    meeting_id, sink, mic_input)


def stop(meeting_id: str) -> float:
    """Stop the active recording; returns the elapsed duration in seconds."""
    global _active
    with _rec_lock:
        if _active is None or _active['meeting_id'] != meeting_id:
            raise RuntimeError('This meeting is not being recorded')
        elapsed = time.monotonic() - _active['started_mono']
        for p in _active['procs']:
            _stop_proc(p)
        if _active.get('ec_state'):
            _teardown_echo_cancel(_active['ec_state'])
        _active = None
        logger.info('Meeting %s: recording stopped after %.1fs', meeting_id, elapsed)
        return elapsed


def active_meeting_id() -> str | None:
    a = _active
    return a['meeting_id'] if a else None
