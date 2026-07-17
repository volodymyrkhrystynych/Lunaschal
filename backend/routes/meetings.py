import json
import subprocess
import tempfile
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.meetings import recorder, storage
from backend.meetings.merge import render_transcript
from backend.meetings.pipeline import start_pipeline
from backend.meetings.recorder import RecorderBusy

bp = Blueprint('meetings', __name__, url_prefix='/api/meetings')

_LIST_COLS = (
    "id, title, status, phase, error, duration_seconds, "
    "notes != '' AS has_notes, summary IS NOT NULL AS has_summary, "
    "started_at, ended_at, created_at, updated_at"
)


@bp.post('/start')
def start_meeting():
    db = get_db()
    if recorder.active_meeting_id() is not None:
        return jsonify({'error': 'A meeting is already being recorded'}), 409
    meeting_id = str(ULID())
    now = int(time.time())
    db.execute(
        'INSERT INTO meetings(id, status, phase, started_at, created_at, updated_at)'
        " VALUES (?, 'recording', 'recording', ?, ?, ?)",
        (meeting_id, now, now, now),
    )
    db.commit()
    try:
        recorder.start(meeting_id)
    except RecorderBusy:
        db.execute('DELETE FROM meetings WHERE id=?', (meeting_id,))
        db.commit()
        return jsonify({'error': 'A meeting is already being recorded'}), 409
    except Exception as e:
        db.execute('DELETE FROM meetings WHERE id=?', (meeting_id,))
        db.commit()
        return jsonify({'error': str(e)}), 500
    return jsonify({'id': meeting_id}), 201


@bp.post('/<id>/stop')
def stop_meeting(id):
    db = get_db()
    row = db.execute('SELECT id, status FROM meetings WHERE id=?', (id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Meeting not found'}), 404
    if row['status'] != 'recording' or recorder.active_meeting_id() != id:
        return jsonify({'error': 'This meeting is not being recorded'}), 409
    duration = recorder.stop(id)
    now = int(time.time())
    db.execute(
        "UPDATE meetings SET status='transcribing', phase='awaiting_start',"
        ' ended_at=?, duration_seconds=?, updated_at=? WHERE id=?',
        (now, duration, now, id),
    )
    db.commit()
    return jsonify({'success': True})


@bp.post('/upload')
def upload_meeting():
    file = request.files.get('audio')
    if not file or not file.filename:
        return jsonify({'error': 'No audio file provided'}), 400
    title = (request.form.get('title') or '').strip() or None

    meeting_id = str(ULID())
    d = storage.meeting_dir(meeting_id)
    if d is None:
        return jsonify({'error': 'Invalid meeting id'}), 500
    d.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename).suffix or '.audio'
    with tempfile.NamedTemporaryFile(dir=d, suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    file.save(tmp_path)
    try:
        duration = storage.transcode_to_system_track(tmp_path, storage.system_path(meeting_id))
    except subprocess.CalledProcessError:
        storage.delete_meeting_dir(meeting_id)
        return jsonify({'error': 'Could not read that audio file'}), 400
    finally:
        tmp_path.unlink(missing_ok=True)

    now = int(time.time())
    started_at = now - int(duration)
    db = get_db()
    db.execute(
        "INSERT INTO meetings(id, title, status, phase, source, started_at, ended_at,"
        " duration_seconds, created_at, updated_at)"
        " VALUES (?, ?, 'transcribing', 'awaiting_start', 'upload', ?, ?, ?, ?, ?)",
        (meeting_id, title, started_at, now, duration, now, now),
    )
    db.commit()
    return jsonify({'id': meeting_id}), 201


@bp.post('/<id>/start-transcription')
def start_transcription(id):
    body = request.json or {}
    model = (body.get('whisperModel') or '').strip()
    device = (body.get('device') or '').strip().lower()
    if not model:
        return jsonify({'error': 'whisperModel is required'}), 400
    if device not in ('cpu', 'cuda'):
        return jsonify({'error': 'device must be "cpu" or "cuda"'}), 400

    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "UPDATE meetings SET status='transcribing', updated_at=?,"
        ' whisper_model=?, whisper_device=?,'
        " phase = CASE WHEN source='upload' THEN 'transcribing_system' ELSE 'transcribing_mic' END"
        " WHERE id=? AND phase='awaiting_start'",
        (now, model, device, id),
    )
    db.commit()
    if cur.rowcount == 0:
        row = db.execute('SELECT id FROM meetings WHERE id=?', (id,)).fetchone()
        if row is None:
            return jsonify({'error': 'Meeting not found'}), 404
        return jsonify({'error': 'Meeting is not awaiting transcription'}), 409
    start_pipeline(id)
    return jsonify({'success': True})


@bp.post('/<id>/retry')
def retry_meeting(id):
    """Re-attempts a failed meeting, typically with a different model/device
    (e.g. after a CUDA OOM). `phase` was left untouched by the failure, so it
    already records the correct resume point (mid-track offsets are still
    checkpointed) — this just clears the error and re-enters the pipeline."""
    body = request.json or {}
    model = (body.get('whisperModel') or '').strip()
    device = (body.get('device') or '').strip().lower()
    if not model:
        return jsonify({'error': 'whisperModel is required'}), 400
    if device not in ('cpu', 'cuda'):
        return jsonify({'error': 'device must be "cpu" or "cuda"'}), 400

    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "UPDATE meetings SET status='transcribing', updated_at=?,"
        " whisper_model=?, whisper_device=?, error=NULL"
        " WHERE id=? AND status='error'",
        (now, model, device, id),
    )
    db.commit()
    if cur.rowcount == 0:
        row = db.execute('SELECT id FROM meetings WHERE id=?', (id,)).fetchone()
        if row is None:
            return jsonify({'error': 'Meeting not found'}), 404
        return jsonify({'error': 'Meeting has not failed'}), 409
    start_pipeline(id)
    return jsonify({'success': True})


@bp.post('/<id>/redo')
def redo_meeting(id):
    """Fully restarts transcription from scratch — for when the user wants a
    completely fresh pass (e.g. a better model on a finished meeting) rather
    than /retry's checkpoint-resume. Discards the existing transcript,
    summary, speaker renames, and chunk checkpoints; the audio itself is
    untouched."""
    body = request.json or {}
    model = (body.get('whisperModel') or '').strip()
    device = (body.get('device') or '').strip().lower()
    if not model:
        return jsonify({'error': 'whisperModel is required'}), 400
    if device not in ('cpu', 'cuda'):
        return jsonify({'error': 'device must be "cpu" or "cuda"'}), 400

    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "UPDATE meetings SET status='transcribing', updated_at=?,"
        " whisper_model=?, whisper_device=?, error=NULL,"
        " segments=NULL, transcript_text=NULL, summary=NULL, speaker_names=NULL,"
        " pause_requested=0, mic_offset_seconds=0, mic_segments_partial=NULL,"
        " system_offset_seconds=0, system_segments_partial=NULL,"
        " phase = CASE WHEN source='upload' THEN 'transcribing_system' ELSE 'transcribing_mic' END"
        " WHERE id=? AND status IN ('done','error')",
        (now, model, device, id),
    )
    db.commit()
    if cur.rowcount == 0:
        row = db.execute('SELECT id FROM meetings WHERE id=?', (id,)).fetchone()
        if row is None:
            return jsonify({'error': 'Meeting not found'}), 404
        return jsonify({'error': 'Meeting is not finished processing'}), 409
    start_pipeline(id)
    return jsonify({'success': True})


@bp.post('/<id>/pause')
def pause_meeting(id):
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "UPDATE meetings SET pause_requested=1, updated_at=? WHERE id=?"
        " AND phase IN ('transcribing_mic','transcribing_system')",
        (now, id),
    )
    db.commit()
    if cur.rowcount == 0:
        row = db.execute('SELECT id FROM meetings WHERE id=?', (id,)).fetchone()
        if row is None:
            return jsonify({'error': 'Meeting not found'}), 404
        return jsonify({'error': 'Meeting is not currently transcribing'}), 409
    return jsonify({'success': True})


@bp.post('/<id>/resume')
def resume_meeting(id):
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "UPDATE meetings SET status='transcribing', updated_at=?,"
        " phase = CASE phase WHEN 'paused_mic' THEN 'transcribing_mic'"
        "                    WHEN 'paused_system' THEN 'transcribing_system' END"
        " WHERE id=? AND phase IN ('paused_mic','paused_system')",
        (now, id),
    )
    db.commit()
    if cur.rowcount == 0:
        row = db.execute('SELECT id FROM meetings WHERE id=?', (id,)).fetchone()
        if row is None:
            return jsonify({'error': 'Meeting not found'}), 404
        return jsonify({'error': 'Meeting is not paused'}), 409
    start_pipeline(id)
    return jsonify({'success': True})


@bp.get('')
def list_meetings():
    rows = get_db().execute(
        f'SELECT {_LIST_COLS} FROM meetings ORDER BY created_at DESC'
    ).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d['hasNotes'] = bool(d.get('hasNotes'))
        d['hasSummary'] = bool(d.get('hasSummary'))
        out.append(d)
    return jsonify(out)


@bp.get('/active')
def active_meeting():
    meeting_id = recorder.active_meeting_id()
    started_at = None
    if meeting_id:
        row = get_db().execute(
            'SELECT started_at FROM meetings WHERE id=?', (meeting_id,)
        ).fetchone()
        if row:
            started_at = row_to_dict(row)['startedAt']
    return jsonify({'id': meeting_id, 'startedAt': started_at})


@bp.get('/<id>')
def get_meeting(id):
    row = get_db().execute('SELECT * FROM meetings WHERE id=?', (id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Meeting not found'}), 404
    d = row_to_dict(row)
    d['segments'] = json.loads(d['segments']) if d.get('segments') else None
    d['speakerNames'] = json.loads(d['speakerNames']) if d.get('speakerNames') else None
    for k in ('pauseRequested', 'micOffsetSeconds', 'micSegmentsPartial',
              'systemOffsetSeconds', 'systemSegmentsPartial'):
        d.pop(k, None)
    return jsonify(d)


@bp.patch('/<id>')
def update_meeting(id):
    body = request.json or {}
    updates = {'updated_at': int(time.time())}
    for field in ('title', 'notes'):
        if field in body:
            updates[field] = body[field]
    if 'speakerNames' in body:
        names = body['speakerNames']
        if names is not None and (
            not isinstance(names, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in names.items())
        ):
            return jsonify({'error': 'speakerNames must be a mapping of speaker label to name'}), 400
        updates['speaker_names'] = json.dumps(names) if names else None
    db = get_db()
    set_clause = ', '.join(f'{k}=?' for k in updates)
    cur = db.execute(f'UPDATE meetings SET {set_clause} WHERE id=?',
                     [*updates.values(), id])
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'Meeting not found'}), 404
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_meeting(id):
    db = get_db()
    row = db.execute('SELECT status FROM meetings WHERE id=?', (id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Meeting not found'}), 404
    if row['status'] == 'recording':
        return jsonify({'error': 'Stop the recording before deleting'}), 409
    db.execute('DELETE FROM meetings WHERE id=?', (id,))
    db.commit()
    storage.delete_meeting_dir(id)
    return jsonify({'success': True})


@bp.get('/<id>/audio/<track>')
def meeting_audio(id, track):
    if track not in ('mic', 'system'):
        return jsonify({'error': 'Unknown track'}), 404
    path = storage.mic_path(id) if track == 'mic' else storage.system_path(id)
    if path is None or not path.is_file():
        return jsonify({'error': 'Audio not found'}), 404
    return send_file(path, mimetype='audio/wav', conditional=True)


@bp.post('/<id>/summarize')
def summarize(id):
    db = get_db()
    row = db.execute(
        'SELECT status, transcript_text, segments, speaker_names FROM meetings WHERE id=?', (id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Meeting not found'}), 404
    if row['status'] != 'done':
        return jsonify({'error': 'Meeting is not finished processing'}), 409
    text = row['transcript_text'] or ''
    # Substitute user-assigned speaker names so the summary says "Alice",
    # not "Speaker 1".
    if row['speaker_names'] and row['segments']:
        names = json.loads(row['speaker_names'])
        segments = json.loads(row['segments'])
        text = render_transcript(
            [{**s, 'speaker': names.get(s['speaker'], s['speaker'])} for s in segments]
        )
    from backend.ai.meetings import summarize_meeting
    summary = summarize_meeting(text)
    if summary is None:
        return jsonify({'error': 'Summarization failed — is an AI provider configured?'}), 502
    db.execute('UPDATE meetings SET summary=?, updated_at=? WHERE id=?',
               (summary, int(time.time()), id))
    db.commit()
    return jsonify({'summary': summary})
