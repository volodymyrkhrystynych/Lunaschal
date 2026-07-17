"""Background transcription pipeline for meetings.

A recording (or upload) sits in phase='awaiting_start' until the user picks a
Whisper model/device and calls POST /<id>/start-transcription — this module
then runs in a daemon thread: transcribes both tracks with the chosen model,
diarizes the system track with pyannote when available, merges everything into
a speaker-labeled transcript, then generates an AI summary. Progress is
persisted in the meetings.phase column so any request can report it.

Whisper transcription runs in fixed-size chunks rather than one whole-file
call, so it can be paused between chunks and resumed later — even after the
app is fully restarted — by checkpointing the running offset and segment list
to the meetings row after every chunk.
"""
import gc
import json
import logging
import os
import threading
import time
from pathlib import Path

from backend.db.connection import get_db
from backend.meetings import storage
from backend.meetings.merge import merge_segments, render_transcript

logger = logging.getLogger(__name__)

WHISPER_MODEL = 'large-v3'
_MIN_AUDIO_BYTES = 16000  # ~0.5s of 16kHz mono s16le — anything less is silence
# Matches whisper's own internal decode window (N_SAMPLES / SAMPLE_RATE), so
# chunking at this size adds no extra fragmentation beyond what whisper
# already imposes on itself internally — a larger chunk would only raise the
# pause-latency floor for no transcription-quality benefit.
_CHUNK_SECONDS = 30
_SAMPLE_RATE = 16000  # audio tracks are always 16kHz mono (recorder.py, storage.py)
_INITIAL_PROMPT_CHARS = 200


class _Paused(Exception):
    """Raised once a pause has been detected and durably checkpointed; the
    thread simply exits — the DB row is already the sole source of truth."""


def start_pipeline(meeting_id: str) -> None:
    threading.Thread(target=_run, args=(meeting_id,), daemon=True).start()


def _set_phase(meeting_id: str, phase: str, *, status: str | None = None, **cols) -> None:
    updates = {'phase': phase, 'updated_at': int(time.time()), **cols}
    if status is not None:
        updates['status'] = status
    db = get_db()
    set_clause = ', '.join(f'{k}=?' for k in updates)
    db.execute(f'UPDATE meetings SET {set_clause} WHERE id=?',
               [*updates.values(), meeting_id])
    db.commit()


def _set_error(meeting_id: str, error: str) -> None:
    """Marks the meeting failed without touching `phase` — phase already
    records exactly which track/stage was in flight (transcribing_mic,
    transcribing_system, diarizing, summarizing), which is exactly the resume
    point /retry needs. Overwriting it would lose that."""
    db = get_db()
    db.execute(
        "UPDATE meetings SET status='error', error=?, updated_at=? WHERE id=?",
        (error, int(time.time()), meeting_id),
    )
    db.commit()


def _get_hf_token() -> str | None:
    try:
        row = get_db().execute('SELECT hf_token FROM settings LIMIT 1').fetchone()
        if row and row['hf_token']:
            return row['hf_token']
    except Exception:
        pass
    return os.environ.get('HF_TOKEN') or None


def _is_pause_requested(meeting_id: str) -> bool:
    row = get_db().execute(
        'SELECT pause_requested FROM meetings WHERE id=?', (meeting_id,)).fetchone()
    return bool(row and row['pause_requested'])


def _transcribe_track_resumable(model, meeting_id: str, path: Path | None, track: str,
                                *, initial_offset: float, initial_segments: list[dict]) -> list[dict]:
    """Transcribes `path` (the 'mic' or 'system' track) in `_CHUNK_SECONDS`
    windows, checkpointing the running offset and accumulated segment list to
    the meetings row after every chunk. Resumes from `initial_offset` /
    `initial_segments` (0 / [] on a fresh start). Raises `_Paused` (after
    persisting a checkpoint) if a pause is requested before the next chunk;
    otherwise returns the finished segment list."""
    if path is None or not path.is_file() or path.stat().st_size < _MIN_AUDIO_BYTES:
        return []

    offset_col = f'{track}_offset_seconds'
    segments_col = f'{track}_segments_partial'
    live_phase = f'transcribing_{track}'
    paused_phase = f'paused_{track}'

    from backend.routes.stt import _transcribe_lock
    import whisper
    audio = whisper.load_audio(str(path))
    total_samples = len(audio)
    chunk_samples = _CHUNK_SECONDS * _SAMPLE_RATE

    segments = list(initial_segments)
    start_sample = int(round(initial_offset * _SAMPLE_RATE))
    detected_language: str | None = None

    while start_sample < total_samples:
        if _is_pause_requested(meeting_id):
            _set_phase(meeting_id, paused_phase, pause_requested=0,
                       **{offset_col: start_sample / _SAMPLE_RATE,
                          segments_col: json.dumps(segments)})
            raise _Paused()

        end_sample = min(start_sample + chunk_samples, total_samples)
        chunk_offset = start_sample / _SAMPLE_RATE
        opts: dict = {'fp16': False}
        if detected_language is not None:
            opts['language'] = detected_language
        if segments:
            # Approximates whisper's own cross-window conditioning, which is
            # otherwise lost by splitting a track into separate transcribe() calls.
            opts['initial_prompt'] = segments[-1]['text'][-_INITIAL_PROMPT_CHARS:]

        # The whisper model is not thread-safe; share stt.py's inference lock so
        # a meeting track never transcribes concurrently with live dictation.
        # Held only per-chunk (not for the whole track) so dictation isn't
        # starved for the entire, potentially long, transcription.
        with _transcribe_lock:
            result = model.transcribe(audio[start_sample:end_sample], **opts)
        detected_language = result.get('language', detected_language)

        segments.extend({'start': chunk_offset + float(s['start']),
                         'end': chunk_offset + float(s['end']),
                         'text': s['text']}
                        for s in result.get('segments', []))
        start_sample = end_sample

        _set_phase(meeting_id, live_phase,
                   **{offset_col: start_sample / _SAMPLE_RATE,
                      segments_col: json.dumps(segments)})

    del audio
    gc.collect()
    return segments


def _diarize(path: Path | None) -> list[dict] | None:
    """Diarize the system track; returns None (→ 'Others' labels) whenever
    pyannote or the HuggingFace token is unavailable."""
    if path is None or not path.is_file() or path.stat().st_size < _MIN_AUDIO_BYTES:
        return None
    token = _get_hf_token()
    if not token:
        logger.info('No HuggingFace token configured — skipping diarization')
        return None
    try:
        from pyannote.audio import Pipeline as PyannotePipeline
        try:
            # pyannote.audio >= 4.0
            pl = PyannotePipeline.from_pretrained('pyannote/speaker-diarization-3.1',
                                                  token=token)
        except TypeError:
            # pyannote.audio 3.x named the argument use_auth_token
            pl = PyannotePipeline.from_pretrained('pyannote/speaker-diarization-3.1',
                                                  use_auth_token=token)
        result = pl(str(path))
        # pyannote.audio >= 4.0 wraps the Annotation in a DiarizeOutput
        annotation = getattr(result, 'speaker_diarization', result)
        return [{'start': float(turn.start), 'end': float(turn.end), 'speaker': label}
                for turn, _, label in annotation.itertracks(yield_label=True)]
    except Exception:
        logger.exception('Diarization unavailable — falling back to "Others" labels')
        return None


def _run(meeting_id: str) -> None:
    try:
        row = get_db().execute(
            'SELECT phase, whisper_model, whisper_device, mic_offset_seconds,'
            ' mic_segments_partial, system_offset_seconds, system_segments_partial'
            ' FROM meetings WHERE id=?',
            (meeting_id,)).fetchone()
        mic = storage.mic_path(meeting_id)
        system = storage.system_path(meeting_id)

        mic_offset = row['mic_offset_seconds'] or 0.0
        mic_partial = json.loads(row['mic_segments_partial']) if row['mic_segments_partial'] else []
        system_offset = row['system_offset_seconds'] or 0.0
        system_partial = json.loads(row['system_segments_partial']) if row['system_segments_partial'] else []

        # A private model instance: going through stt._load_stt would evict the
        # user's configured model from its settings-keyed singleton.
        model_name = row['whisper_model'] or WHISPER_MODEL
        device = row['whisper_device'] or 'cpu'
        import whisper
        logger.info('Meeting %s: loading Whisper %s on %s…', meeting_id, model_name, device)
        model = whisper.load_model(model_name, device=device)

        if row['phase'] == 'transcribing_mic':
            mic_segments = _transcribe_track_resumable(
                model, meeting_id, mic, 'mic',
                initial_offset=mic_offset, initial_segments=mic_partial)
            _set_phase(meeting_id, 'transcribing_system')
            system_segments = _transcribe_track_resumable(
                model, meeting_id, system, 'system', initial_offset=0.0, initial_segments=[])
        else:
            # 'transcribing_system' — the mic track (if any) already finished,
            # either in an earlier chunk-loop pass or because this meeting
            # (an upload) never had one.
            mic_segments = mic_partial
            system_segments = _transcribe_track_resumable(
                model, meeting_id, system, 'system',
                initial_offset=system_offset, initial_segments=system_partial)

        del model
        gc.collect()

        _set_phase(meeting_id, 'diarizing', pause_requested=0)
        turns = _diarize(system)

        segments = merge_segments(mic_segments, system_segments, turns)
        text = render_transcript(segments)
        _set_phase(meeting_id, 'summarizing',
                   segments=json.dumps(segments), transcript_text=text)

        from backend.ai.meetings import summarize_meeting
        summary = summarize_meeting(text)

        if summary is not None:
            _set_phase(meeting_id, 'done', status='done', summary=summary)
        else:
            _set_phase(meeting_id, 'done', status='done')
        logger.info('Meeting %s: transcription pipeline finished', meeting_id)
    except _Paused:
        logger.info('Meeting %s: transcription paused', meeting_id)
    except Exception as e:
        logger.exception('Meeting %s: transcription pipeline failed', meeting_id)
        try:
            _set_error(meeting_id, str(e))
        except Exception:
            pass
