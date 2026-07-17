"""Background transcription pipeline for meetings.

Runs in a daemon thread after a recording stops: transcribes both tracks with
Whisper large-v3 on CPU (deliberately the largest model — slow but accurate),
diarizes the system track with pyannote when available, merges everything into
a speaker-labeled transcript, then generates an AI summary. Progress is
persisted in the meetings.phase column so any request can report it.
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


def _get_hf_token() -> str | None:
    try:
        row = get_db().execute('SELECT hf_token FROM settings LIMIT 1').fetchone()
        if row and row['hf_token']:
            return row['hf_token']
    except Exception:
        pass
    return os.environ.get('HF_TOKEN') or None


def _transcribe_track(model, path: Path | None) -> list[dict]:
    if path is None or not path.is_file() or path.stat().st_size < _MIN_AUDIO_BYTES:
        return []
    # The whisper model is not thread-safe; share stt.py's inference lock so a
    # meeting track never transcribes concurrently with live dictation.
    from backend.routes.stt import _transcribe_lock
    with _transcribe_lock:
        result = model.transcribe(str(path), fp16=False)
    return [{'start': float(s['start']), 'end': float(s['end']), 'text': s['text']}
            for s in result.get('segments', [])]


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
        mic = storage.mic_path(meeting_id)
        system = storage.system_path(meeting_id)
        has_mic = mic is not None and mic.is_file()

        # Uploaded single-track meetings have no separate mic recording — skip
        # straight to the phase label that actually applies.
        _set_phase(meeting_id, 'transcribing_mic' if has_mic else 'transcribing_system',
                   status='transcribing')
        # A private model instance: going through stt._load_stt would evict the
        # user's configured model from its settings-keyed singleton.
        import whisper
        logger.info('Meeting %s: loading Whisper %s on cpu…', meeting_id, WHISPER_MODEL)
        model = whisper.load_model(WHISPER_MODEL, device='cpu')
        mic_segments = _transcribe_track(model, mic)

        if has_mic:
            _set_phase(meeting_id, 'transcribing_system')
        system_segments = _transcribe_track(model, system)
        del model
        gc.collect()

        _set_phase(meeting_id, 'diarizing')
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
    except Exception as e:
        logger.exception('Meeting %s: transcription pipeline failed', meeting_id)
        try:
            _set_phase(meeting_id, 'error', status='error', error=str(e))
        except Exception:
            pass
