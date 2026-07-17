import json
import time

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
        "UPDATE meetings SET status='transcribing', phase='transcribing_mic',"
        ' ended_at=?, duration_seconds=?, updated_at=? WHERE id=?',
        (now, duration, now, id),
    )
    db.commit()
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
