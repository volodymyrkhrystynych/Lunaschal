import time
import json
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend.db.connection import get_db, row_to_dict
from backend.auth import require_auth

bp = Blueprint('calendar', __name__, url_prefix='/api/calendar')


@bp.get('')
@require_auth
def list_by_range():
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    db = get_db()
    rows = db.execute(
        'SELECT * FROM calendar_events WHERE date BETWEEN ? AND ? ORDER BY date, time',
        (start, end),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/date/<date>')
@require_auth
def list_by_date(date):
    rows = get_db().execute(
        'SELECT * FROM calendar_events WHERE date=? ORDER BY time',
        (date,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/week/<date>')
@require_auth
def list_by_week(date):
    from datetime import date as dt, timedelta
    d = dt.fromisoformat(date)
    start = d - timedelta(days=d.weekday() + 1 if d.weekday() != 6 else 0)
    # Sunday-based week to match JS Date.getDay() behavior
    day_of_week = (d.weekday() + 1) % 7  # 0=Sun
    start = d - timedelta(days=day_of_week)
    end = start + timedelta(days=6)
    rows = get_db().execute(
        'SELECT * FROM calendar_events WHERE date BETWEEN ? AND ? ORDER BY date, time',
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/related-journals/<date>')
@require_auth
def related_journals(date):
    import time as t
    from datetime import datetime, timezone
    start = int(datetime.fromisoformat(f'{date}T00:00:00+00:00').timestamp())
    end = int(datetime.fromisoformat(f'{date}T23:59:59+00:00').timestamp())
    rows = get_db().execute(
        'SELECT * FROM journal_entries WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC',
        (start, end),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/<id>')
@require_auth
def get_event(id):
    db = get_db()
    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    event = row_to_dict(row)

    # Get linked journals via many-to-many table
    links = db.execute(
        'SELECT journal_entry_id FROM calendar_journal_links WHERE calendar_event_id=?', (id,)
    ).fetchall()
    linked_ids = {l['journal_entry_id'] for l in links}

    # Also include direct journal link
    if event.get('journalId'):
        linked_ids.add(event['journalId'])

    linked_journals = []
    if linked_ids:
        ph = ','.join('?' * len(linked_ids))
        jrows = db.execute(f'SELECT * FROM journal_entries WHERE id IN ({ph})', list(linked_ids)).fetchall()
        linked_journals = [row_to_dict(r) for r in jrows]

    event['linkedJournals'] = linked_journals
    return jsonify(event)


@bp.post('')
@require_auth
def create_event():
    body = request.json or {}
    if not body.get('title') or not body.get('date'):
        return jsonify({'error': 'title and date required'}), 400
    now = int(time.time())
    id = str(ULID())
    tags = body.get('tags')
    get_db().execute(
        'INSERT INTO calendar_events(id, title, description, date, time, end_time, tags, journal_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (id, body['title'], body.get('description'), body['date'], body.get('time'),
         body.get('endTime'), json.dumps(tags) if tags is not None else None, body.get('journalId'), now),
    )
    get_db().commit()
    return jsonify({'id': id}), 201


@bp.patch('/<id>')
@require_auth
def update_event(id):
    body = request.json or {}
    field_map = {
        'title': 'title', 'description': 'description', 'date': 'date',
        'time': 'time', 'endTime': 'end_time', 'journalId': 'journal_id',
    }
    updates: dict = {}
    for camel, snake in field_map.items():
        if camel in body:
            updates[snake] = body[camel]
    if 'tags' in body:
        updates['tags'] = json.dumps(body['tags'])
    if not updates:
        return jsonify({'success': True})
    set_clause = ', '.join(f'{k}=?' for k in updates)
    get_db().execute(f'UPDATE calendar_events SET {set_clause} WHERE id=?', [*updates.values(), id])
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/<id>')
@require_auth
def delete_event(id):
    get_db().execute('DELETE FROM calendar_events WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.post('/<id>/link')
@require_auth
def link_journal(id):
    body = request.json or {}
    journal_id = body.get('journalEntryId')
    if not journal_id:
        return jsonify({'error': 'journalEntryId required'}), 400
    db = get_db()
    existing = db.execute(
        'SELECT id FROM calendar_journal_links WHERE calendar_event_id=? AND journal_entry_id=?',
        (id, journal_id),
    ).fetchone()
    if existing:
        return jsonify({'id': existing['id']})
    link_id = str(ULID())
    db.execute(
        'INSERT INTO calendar_journal_links(id, calendar_event_id, journal_entry_id, created_at) VALUES (?,?,?,?)',
        (link_id, id, journal_id, int(time.time())),
    )
    db.commit()
    return jsonify({'id': link_id}), 201


@bp.delete('/<id>/link/<journal_id>')
@require_auth
def unlink_journal(id, journal_id):
    get_db().execute(
        'DELETE FROM calendar_journal_links WHERE calendar_event_id=? AND journal_entry_id=?',
        (id, journal_id),
    )
    get_db().commit()
    return jsonify({'success': True})
