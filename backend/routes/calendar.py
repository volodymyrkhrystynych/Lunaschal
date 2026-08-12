import time
import json
from datetime import date as dt, datetime, timedelta
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend import sleep
from backend.ai.background import run_bg
from backend.calendar_query import events_in_range
from backend.calendar_recurrence import VALID_FREQS, format_byweekday
from backend.db.connection import build_update, get_db, mapping_to_dict, row_to_dict

bp = Blueprint('calendar', __name__, url_prefix='/api/calendar')


def _valid_date(value) -> bool:
    try:
        dt.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _valid_time(value) -> bool:
    """'HH:MM' (or 'HH:MM:SS', which <input type=time> emits with step set)."""
    try:
        datetime.strptime(str(value)[:5], '%H:%M')
        return True
    except (TypeError, ValueError):
        return False


def _all_day_field(body: dict) -> tuple[dict, str | None]:
    """Validate and normalise the all-day half of a create/update body.

    Turning the flag on clears any stored clock times: an all-day event that
    also starts at 14:00 is a contradiction, and leaving a stale time behind
    would resurface the moment the flag was turned back off.
    """
    if 'allDay' not in body:
        return {}, None
    if not body['allDay']:
        return {'all_day': 0}, None
    if body.get('time') or body.get('endTime'):
        return {}, 'an all-day event cannot have a time'
    return {'all_day': 1, 'time': None, 'end_time': None}, None


def _repeat_fields(body: dict) -> tuple[dict, str | None]:
    """Validate and normalise the recurrence half of a create/update body.

    Returns (columns, error). An explicit `repeatFreq: None` clears the rule,
    which is how the UI turns a series back into a one-off.
    """
    updates: dict = {}
    if 'repeatFreq' in body:
        freq = body['repeatFreq'] or None
        if freq is not None and freq not in VALID_FREQS:
            return {}, f'repeatFreq must be one of {", ".join(VALID_FREQS)}'
        updates['repeat_freq'] = freq
        if freq is None:
            # Clearing the rule clears its parameters, so a later re-enable
            # doesn't inherit stale weekdays or an expired end date.
            updates.update(repeat_interval=None, repeat_byweekday=None, repeat_until=None)
            return updates, None

    if 'repeatInterval' in body:
        interval = body['repeatInterval']
        if interval is not None:
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                return {}, 'repeatInterval must be an integer >= 1'
            if interval < 1:
                return {}, 'repeatInterval must be an integer >= 1'
        updates['repeat_interval'] = interval

    if 'repeatByweekday' in body:
        days = body['repeatByweekday']
        if days is None:
            updates['repeat_byweekday'] = None
        else:
            if not isinstance(days, list):
                return {}, 'repeatByweekday must be a list of 0-6'
            for d in days:
                if not isinstance(d, int) or not 0 <= d <= 6:
                    return {}, 'repeatByweekday must be a list of 0-6'
            updates['repeat_byweekday'] = format_byweekday(days)

    if 'repeatUntil' in body:
        until = body['repeatUntil'] or None
        if until is not None and not _valid_date(until):
            return {}, 'repeatUntil must be YYYY-MM-DD'
        updates['repeat_until'] = until

    return updates, None


@bp.get('')
def list_by_range():
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    return jsonify([_to_json(e) for e in events_in_range(get_db(), start, end)])


@bp.get('/date/<date>')
def list_by_date(date):
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    return jsonify([_to_json(e) for e in events_in_range(get_db(), date, date)])


@bp.get('/week/<date>')
def list_by_week(date):
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    d = dt.fromisoformat(date)
    # Sunday-based week to match JS Date.getDay() behavior
    day_of_week = (d.weekday() + 1) % 7  # 0=Sun
    start = d - timedelta(days=day_of_week)
    end = start + timedelta(days=6)
    events = events_in_range(get_db(), start.isoformat(), end.isoformat())
    return jsonify([_to_json(e) for e in events])


def _to_json(event: dict) -> dict:
    """Snake_case occurrence dict (plus the expander's camelCase extras) -> API shape."""
    extras = {k: event.pop(k) for k in ('occurrenceDate', 'isRecurring') if k in event}
    return {**mapping_to_dict(event), **extras}


@bp.get('/related-journals/<date>')
def related_journals(date):
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    # Journal timestamps are local unix seconds, so the day window has to be
    # built in local time too — a UTC window is off by the local offset.
    start = int(datetime.fromisoformat(f'{date}T00:00:00').timestamp())
    end = int(datetime.fromisoformat(f'{date}T23:59:59').timestamp())
    rows = get_db().execute(
        'SELECT * FROM journal_entries WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC',
        (start, end),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


def _sleep_payload(day_key: str) -> dict:
    """A day's own wake/sleep, plus the two neighbouring ends the day view needs
    to draw its bands: the night before ended at this day's wake, and the night
    after ends at the next day's."""
    prev = (dt.fromisoformat(day_key) - timedelta(days=1)).isoformat()
    nxt = (dt.fromisoformat(day_key) + timedelta(days=1)).isoformat()
    return {
        **sleep.resolve_day(day_key),
        'previousSleepAt': sleep.resolve_day(prev)['sleepAt'],
        'nextWakeAt': sleep.resolve_day(nxt)['wakeAt'],
    }


@bp.get('/sleep/<date>')
def get_sleep(date):
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    return jsonify(_sleep_payload(date))


@bp.put('/sleep/<date>')
def set_sleep(date):
    """Set the day's manual wake/sleep. The body is the whole manual state for
    that day, so omitting an end (or sending null) hands it back to the derived
    value — the editor submits both fields together."""
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    body = request.get_json(silent=True) or {}
    stamps = {}
    for field in ('wake', 'sleep'):
        value = body.get(field)
        if value is None:
            stamps[field] = None
            continue
        if not _valid_time(value):
            return jsonify({'error': f'{field} must be HH:MM'}), 400
        stamps[field] = sleep.time_to_timestamp(date, str(value))
    if (
        stamps['wake'] is not None
        and stamps['sleep'] is not None
        and stamps['sleep'] <= stamps['wake']
    ):
        return jsonify({'error': 'sleep must come after wake'}), 400
    sleep.set_day(date, wake=stamps['wake'], sleep=stamps['sleep'])
    return jsonify(_sleep_payload(date))


@bp.delete('/sleep/<date>')
def clear_sleep(date):
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    sleep.clear_day(date)
    return jsonify(_sleep_payload(date))


@bp.get('/<id>')
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


@bp.post('/<id>/transcribe')
def transcribe_event(id):
    """Save an already-transcribed recording as the event's description and
    queue AI category classification — no confirm step, matching the other
    record-and-save-immediately voice paths in the app (e.g. the Journal
    button on the bottom SttPanel). `text` is transcribed client-side via the
    existing /api/transcribe endpoint before this is called.
    """
    body = request.json or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400

    db = get_db()
    row = db.execute('SELECT id FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    build_update(
        db, 'calendar_events',
        {'description': text, 'classified_at': None, 'classification_error': None},
        'id=?', (id,),
    )
    db.commit()

    from backend.ai.calendar import classify_event_categories
    run_bg(lambda: classify_event_categories(id))

    updated = db.execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()
    return jsonify(row_to_dict(updated))


@bp.post('')
def create_event():
    body = request.json or {}
    if not body.get('title') or not body.get('date'):
        return jsonify({'error': 'title and date required'}), 400
    if not _valid_date(body['date']):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    for field in ('time', 'endTime'):
        if body.get(field) and not _valid_time(body[field]):
            return jsonify({'error': f'{field} must be HH:MM'}), 400
    repeat, err = _repeat_fields(body)
    if err:
        return jsonify({'error': err}), 400
    if repeat.get('repeat_until') and repeat['repeat_until'] < body['date']:
        return jsonify({'error': 'repeatUntil must not precede date'}), 400
    all_day, err = _all_day_field(body)
    if err:
        return jsonify({'error': err}), 400

    now = int(time.time())
    id = str(ULID())
    tags = body.get('tags')
    is_all_day = all_day.get('all_day', 0)
    get_db().execute(
        '''INSERT INTO calendar_events(
               id, title, description, date, time, end_time, all_day, tags,
               journal_id, created_at,
               repeat_freq, repeat_interval, repeat_byweekday, repeat_until)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (id, body['title'], body.get('description'), body['date'],
         None if is_all_day else body.get('time'),
         None if is_all_day else body.get('endTime'),
         is_all_day, json.dumps(tags) if tags is not None else None,
         body.get('journalId'), now,
         repeat.get('repeat_freq'), repeat.get('repeat_interval'),
         repeat.get('repeat_byweekday'), repeat.get('repeat_until')),
    )
    get_db().commit()
    return jsonify({'id': id}), 201


_FIELD_MAP = {
    'title': 'title', 'description': 'description', 'date': 'date',
    'time': 'time', 'endTime': 'end_time', 'journalId': 'journal_id',
}


def _event_updates(body: dict) -> tuple[dict, str | None]:
    """Validate an edit payload into snake_case columns. Shared by the plain
    PATCH and the "this and future" split so both accept the same fields."""
    if body.get('date') and not _valid_date(body['date']):
        return {}, 'date must be YYYY-MM-DD'
    for field in ('time', 'endTime'):
        if body.get(field) and not _valid_time(body[field]):
            return {}, f'{field} must be HH:MM'

    updates: dict = {}
    for camel, snake in _FIELD_MAP.items():
        if camel in body:
            updates[snake] = body[camel]
    if 'tags' in body:
        updates['tags'] = json.dumps(body['tags'])
    repeat, err = _repeat_fields(body)
    if err:
        return {}, err
    updates.update(repeat)
    # Last, so clearing the times on an all-day switch wins over any time
    # carried in the same payload by _FIELD_MAP.
    all_day, err = _all_day_field(body)
    if err:
        return {}, err
    updates.update(all_day)
    return updates, None


@bp.patch('/<id>')
def update_event(id):
    """Edit every occurrence, past ones included. The UI reserves this for
    "all events"; "this and future" goes through PATCH /<id>/from/<date>."""
    db = get_db()
    row = db.execute('SELECT id FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    updates, err = _event_updates(request.json or {})
    if err:
        return jsonify({'error': err}), 400
    if not updates:
        return jsonify({'success': True})
    build_update(db, 'calendar_events', updates, 'id=?', (id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<id>')
def delete_event(id):
    """Erase the series outright, history included — for one created by mistake.

    To stop an ongoing commitment, use DELETE /<id>/from/<date> instead: what
    already happened is a record of the user's life, not a scheduling artefact.
    """
    db = get_db()
    row = db.execute('SELECT id FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    # Exceptions cascade — deleting the series takes its per-occurrence edits.
    db.execute('DELETE FROM calendar_events WHERE id=?', (id,))
    db.commit()
    return jsonify({'success': True})


def _cap_cutoff(row, date_iso: str) -> str | None:
    """The `repeat_until` that stops a series just before `date_iso`.

    `repeat_until` is inclusive, so the cap is the day before. None means the
    cut lands at or before the anchor and nothing would survive it — there is
    no history to preserve in that case.
    """
    cutoff = (dt.fromisoformat(date_iso) - timedelta(days=1)).isoformat()
    return cutoff if cutoff >= row['date'] else None


@bp.delete('/<id>/from/<date>')
def end_series(id, date):
    """Cancel a recurring event from `date` onward.

    The everyday "I've stopped doing this" action. Past occurrences stay
    exactly as they were, so history, the briefing and anything linked to those
    days are untouched.
    """
    db = get_db()
    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400

    cutoff = _cap_cutoff(row, date)
    if cutoff is None:
        # Cancelled from its very first occurrence: nothing ever happened, so
        # there is no history to protect and the row is just noise.
        db.execute('DELETE FROM calendar_events WHERE id=?', (id,))
        db.commit()
        return jsonify({'success': True, 'deleted': True})

    db.execute('UPDATE calendar_events SET repeat_until=? WHERE id=?', (cutoff, id))
    # Per-occurrence edits past the cap can never fire again.
    db.execute(
        'DELETE FROM calendar_event_exceptions WHERE event_id=? AND date>=?', (id, date))
    db.commit()
    return jsonify({'success': True, 'deleted': False})


# Columns a split copies onto the new series. `journal_id` and the
# calendar_journal_links rows deliberately stay behind: they point at entries
# written about specific past days.
_SPLIT_COLUMNS = (
    'title', 'description', 'time', 'end_time', 'all_day', 'tags',
    'repeat_freq', 'repeat_interval', 'repeat_byweekday', 'repeat_until',
)


@bp.patch('/<id>/from/<date>')
def update_series_from(id, date):
    """Apply changes to `date` and every later occurrence, leaving earlier ones
    with the values they actually had.

    Caps the original series the day before and starts a new one carrying the
    edits, linked back via `split_from`. When the cut lands at or before the
    anchor there is no past to preserve, so the row is edited in place.
    """
    db = get_db()
    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400

    body = request.json or {}
    updates, err = _event_updates(body)
    if err:
        return jsonify({'error': err}), 400
    if not updates:
        return jsonify({'id': id, 'split': False})

    cutoff = _cap_cutoff(row, date)
    if row['repeat_freq'] is None or cutoff is None:
        # A one-off, or a cut with no earlier occurrences: nothing to preserve,
        # so edit in place rather than leaving an empty husk of a series behind.
        build_update(db, 'calendar_events', updates, 'id=?', (id,))
        db.commit()
        return jsonify({'id': id, 'split': False})

    values = {col: row[col] for col in _SPLIT_COLUMNS}
    values.update({k: v for k, v in updates.items() if k in _SPLIT_COLUMNS})

    new_id = str(ULID())
    cols = ', '.join(_SPLIT_COLUMNS)
    ph = ','.join('?' * len(_SPLIT_COLUMNS))
    db.execute(
        f'INSERT INTO calendar_events(id, date, created_at, split_from, {cols}) '
        f'VALUES (?,?,?,?,{ph})',
        (new_id, date, int(time.time()), id, *(values[c] for c in _SPLIT_COLUMNS)),
    )
    # Per-occurrence edits on or after the cut belong to the new series. Move
    # them before capping the old one, which would otherwise sweep them away.
    db.execute(
        'UPDATE calendar_event_exceptions SET event_id=? WHERE event_id=? AND date>=?',
        (new_id, id, date),
    )
    db.execute('UPDATE calendar_events SET repeat_until=? WHERE id=?', (cutoff, id))
    db.commit()
    return jsonify({'id': new_id, 'split': True})


def _upsert_exception(event_id: str, date: str, action: str, new: dict) -> tuple[dict, int]:
    db = get_db()
    row = db.execute('SELECT id FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    if not row:
        return {'error': 'Not found'}, 404
    if not _valid_date(date):
        return {'error': 'date must be YYYY-MM-DD'}, 400
    db.execute(
        '''INSERT INTO calendar_event_exceptions(
               id, event_id, date, action, new_date, new_time, new_end_time, created_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(event_id, date) DO UPDATE SET
               action=excluded.action, new_date=excluded.new_date,
               new_time=excluded.new_time, new_end_time=excluded.new_end_time''',
        (str(ULID()), event_id, date, action, new.get('date'),
         new.get('time'), new.get('endTime'), int(time.time())),
    )
    db.commit()
    return {'success': True}, 200


@bp.delete('/<id>/occurrence/<date>')
def skip_occurrence(id, date):
    """Drop a single occurrence of a series ('not working that day')."""
    body, status = _upsert_exception(id, date, 'skip', {})
    return jsonify(body), status


@bp.patch('/<id>/occurrence/<date>')
def move_occurrence(id, date):
    """Reschedule a single occurrence without touching the rest of the series."""
    body = request.json or {}
    if body.get('newDate') and not _valid_date(body['newDate']):
        return jsonify({'error': 'newDate must be YYYY-MM-DD'}), 400
    for field in ('newTime', 'newEndTime'):
        if body.get(field) and not _valid_time(body[field]):
            return jsonify({'error': f'{field} must be HH:MM'}), 400
    result, status = _upsert_exception(id, date, 'move', {
        'date': body.get('newDate'),
        'time': body.get('newTime'),
        'endTime': body.get('newEndTime'),
    })
    return jsonify(result), status


@bp.post('/<id>/link')
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
def unlink_journal(id, journal_id):
    get_db().execute(
        'DELETE FROM calendar_journal_links WHERE calendar_event_id=? AND journal_entry_id=?',
        (id, journal_id),
    )
    get_db().commit()
    return jsonify({'success': True})
