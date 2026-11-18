import json
import re
import time
from datetime import date as dt, datetime, timedelta
from flask import Blueprint, jsonify, request
from ulid import ULID
from backend import activity_state, authorship, calendar_media, sleep
from backend.ai import jobs
from backend.ai.calendar import EVENT_CATEGORIES
from backend.calendar_query import events_in_range
from backend.calendar_recurrence import VALID_FREQS, format_byweekday
from backend.day_boundary import day_bounds
from backend.db.connection import build_update, get_db, mapping_to_dict, row_to_dict
from backend.tags import tag_counts, tags_json

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


def _category_tags_field(body: dict) -> tuple[dict, str | None]:
    """Validate a manually-set `categoryTags` list against the same closed
    vocabulary the AI classifier uses (backend/ai/calendar.py), so a manual
    edit can't put the column in a state the classifier itself never
    would. Setting it by hand also stamps `classified_at` and clears any
    `classification_error` — it supersedes whatever the classifier did or
    failed to do, the same way a fresh transcription resets both."""
    if 'categoryTags' not in body:
        return {}, None
    raw = body['categoryTags']
    if raw is None:
        return {'category_tags': None, 'classified_at': int(time.time()), 'classification_error': None}, None
    if not isinstance(raw, list) or any(c not in EVENT_CATEGORIES for c in raw):
        return {}, f'categoryTags must be a list from {", ".join(EVENT_CATEGORIES)}'
    categories = list(dict.fromkeys(raw))[:3]
    return {
        'category_tags': json.dumps(categories) if categories else None,
        'classified_at': int(time.time()),
        'classification_error': None,
    }, None


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
    db = get_db()
    return jsonify(authorship.attach(db, 'calendar_events',
                                     [_to_json(e) for e in events_in_range(db, start, end)]))


@bp.get('/date/<date>')
def list_by_date(date):
    if not _valid_date(date):
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400
    db = get_db()
    return jsonify(authorship.attach(db, 'calendar_events',
                                     [_to_json(e) for e in events_in_range(db, date, date)]))


@bp.get('/tags')
def list_tags():
    """Every free-text tag in use, with counts — the pill row's vocabulary.

    Deliberately the whole table rather than the month on screen: a filter that
    only offers the tags of whatever you happen to be looking at cannot be used
    to go and find the others. Same shape and same helper as the cookbook and
    food-log pill endpoints.
    """
    rows = get_db().execute(
        'SELECT tags FROM calendar_events WHERE tags IS NOT NULL').fetchall()
    return jsonify(tag_counts(rows))


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
    # Journal timestamps are local unix seconds, and "day" here is the app-wide
    # 4am-anchored day, not literal midnight — matches backend/routes/journal.py's
    # merge_candidates.
    start, end = day_bounds(date)
    rows = get_db().execute(
        'SELECT * FROM journal_entries WHERE created_at >= ? AND created_at < ? ORDER BY created_at DESC',
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


@bp.get('/reading/<date>')
def get_reading(date):
    """Recorded leisure, projected directly onto the 4am calendar day."""
    if not _valid_date(date):
        return jsonify({'error': 'invalid date'}), 400
    start, end = day_bounds(date)
    rows = get_db().execute(
        'SELECT s.id, s.fic_id, s.started_at, s.ended_at, f.title, c.position AS chapter_number'
        ' FROM fic_reading_spans s JOIN fics f ON f.id=s.fic_id'
        ' JOIN fic_chapters c ON c.id=s.chapter_id'
        ' WHERE s.started_at<? AND s.ended_at>? AND s.active_seconds>=60'
        ' ORDER BY s.started_at, s.id', (end, start))
    return jsonify([{
        'id': row['id'], 'ficId': row['fic_id'], 'title': row['title'],
        'chapterNumber': row['chapter_number'],
        'startedAt': max(start, row['started_at']),
        'endedAt': min(end, row['ended_at']),
    } for row in rows])


@bp.get('/activity/<date>')
def get_activity(date):
    """Work / leisure / mix across the waking day, the rail beside reading's.
    Work that overlaps reading comes back as mix."""
    if not _valid_date(date):
        return jsonify({'error': 'invalid date'}), 400
    return jsonify(activity_state.day_spans(get_db(), date))


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


def _stored_tags(raw) -> list:
    """The tags column as a list, whatever state it is in. Guarded because the
    only thing this route must never do is fail: the recording is already in
    hand, and a legacy row with unparseable JSON is not a reason to drop it."""
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


#: Columns a spoken edit is allowed to write, keyed by the camelCase field
#: backend/calendar_voice.py emits. `date` is absent by construction — see that
#: module's docstring for why the mic button must not move an event off the day
#: it was spoken at.
_VOICE_EDIT_COLUMNS = {
    'title': 'title', 'description': 'description',
    'time': 'time', 'endTime': 'end_time', 'tags': 'tags',
}


@bp.post('/<id>/transcribe')
def transcribe_event(id):
    """Apply a spoken sentence to one event — no confirm step, matching the
    other record-and-save-immediately voice paths in the app (e.g. the Journal
    button on the bottom SttPanel). `text` is transcribed client-side via the
    existing /api/transcribe endpoint before this is called.

    The sentence is read as an edit (title/description/time/endTime/tags —
    never the date) rather than being stored verbatim. When nothing usable
    comes back — AI unconfigured, the model down, a sentence that asked for
    nothing this may change — it falls through to what this endpoint has
    always done and stores the transcript as the description, so the recording
    is never the thing that gets lost.

    A time change on one occurrence of a recurring series becomes an exception
    row rather than an edit to the series, which is the same scoping a drag in
    the day view already applies; the other fields belong to the series.
    """
    body = request.json or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    occurrence_date = body.get('occurrenceDate')
    if occurrence_date is not None and not _valid_date(occurrence_date):
        return jsonify({'error': 'occurrenceDate must be YYYY-MM-DD'}), 400

    db = get_db()
    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    from backend.ai.calendar import classify_event_categories, parse_event_voice_edit

    current = {
        'title': row['title'],
        'description': row['description'],
        'time': row['time'],
        'endTime': row['end_time'],
        'tags': _stored_tags(row['tags']),
    }
    edit = parse_event_voice_edit(current, text)
    # What the model rewrote is its text; the fallback is the sentence itself,
    # transcribed (the clip is not kept, so it names no recording).
    voice_author = authorship.AI
    if not edit:
        edit = {'description': text}
        voice_author = authorship.SPEECH

    # Times on a single occurrence of a series go to an exception row; the rest
    # of the edit is a property of the series and goes to the row itself.
    occurrence_times = {}
    is_series = row['repeat_freq'] in VALID_FREQS
    if is_series and occurrence_date:
        for field in ('time', 'endTime'):
            if field in edit:
                occurrence_times[field] = edit.pop(field)

    # Iterated over the allowlist rather than over what came back, so the map
    # is a gate and not just a rename: anything the parse did not filter —
    # `date` above all — has no column to be written to.
    updates = {
        column: (tags_json(edit[field]) if field == 'tags' else edit[field])
        for field, column in _VOICE_EDIT_COLUMNS.items()
        if field in edit
    }
    if 'description' in updates:
        # A new description is a new thing to classify; the old categories
        # described text that is no longer there.
        updates['classified_at'] = None
        updates['classification_error'] = None

    if updates:
        if voice_author == authorship.SPEECH:
            # The fallback replaces the whole description with the sentence.
            authorship.mark(db, 'calendar_events', id, 'description', updates['description'],
                            authorship.SPEECH)
        else:
            authorship.record_edits(db, 'calendar_events', id, updates, author=authorship.AI)
        build_update(db, 'calendar_events', updates, 'id=?', (id,))
        db.commit()

    if occurrence_times:
        # Carried forward rather than left to default: _upsert_exception writes
        # all three `new_*` columns, so omitting the date on an occurrence that
        # a drag had already moved across the midnight rule would quietly move
        # it back.
        existing = db.execute(
            'SELECT new_date, new_time, new_end_time FROM calendar_event_exceptions'
            ' WHERE event_id=? AND date=?',
            (id, occurrence_date),
        ).fetchone()
        _upsert_exception(id, occurrence_date, 'move', {
            'date': existing['new_date'] if existing else None,
            'time': occurrence_times.get(
                'time', existing['new_time'] if existing else None),
            'endTime': occurrence_times.get(
                'endTime', existing['new_end_time'] if existing else None),
        })

    if 'description' in updates:
        jobs.enqueue('calendar.classify', id)

    updated = db.execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()
    return jsonify({
        **row_to_dict(updated),
        # What the sentence actually moved, so the button can say so rather
        # than leaving the user to spot the difference on the timeline. The
        # occurrence-scoped times are named here too — they changed, they just
        # did not change on this row.
        'voiceEdit': {
            'applied': sorted(
                [f for f in edit if f in _VOICE_EDIT_COLUMNS] + list(occurrence_times)
            ),
            'transcript': text,
        },
    })


@bp.post('')
def create_event():
    body = request.json or {}
    title = str(body.get('title') or '').strip()
    # An event that is being spoken can start blank: its clips follow this
    # create, and the title comes from what they say.
    clips_follow = _clip_count(body.get('pendingClips')) > 0
    if (not title and not clips_follow) or not body.get('date'):
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
    category_tags, err = _category_tags_field(body)
    if err:
        return jsonify({'error': err}), 400

    now = int(time.time())
    # A client-supplied ULID makes an offline-queued create replay idempotently:
    # INSERT OR IGNORE turns a duplicate into a no-op, so a replay that the
    # server already applied (an answer lost on the way back) cannot produce a
    # second event. Same contract as journal, todos and learning.
    id = body.get('id') or str(ULID())
    tags = body.get('tags')
    is_all_day = all_day.get('all_day', 0)
    db = get_db()
    inserted = db.execute(
        '''INSERT OR IGNORE INTO calendar_events(
               id, title, description, date, time, end_time, all_day, tags,
               journal_id, created_at,
               repeat_freq, repeat_interval, repeat_byweekday, repeat_until,
               category_tags, classified_at, classification_error)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (id, title, body.get('description'), body['date'],
         None if is_all_day else body.get('time'),
         None if is_all_day else body.get('endTime'),
         is_all_day, tags_json(tags),
         body.get('journalId'), now,
         repeat.get('repeat_freq'), repeat.get('repeat_interval'),
         repeat.get('repeat_byweekday'), repeat.get('repeat_until'),
         category_tags.get('category_tags'), category_tags.get('classified_at'),
         category_tags.get('classification_error')),
    ).rowcount
    # Not on a replay, and not for categories chosen by hand. A spoken event is
    # classified now on whatever was typed, not held for its clips: a clip that
    # never arrives would otherwise leave it unclassified for good, and one that
    # does reopens classification when its transcript lands (_append_description).
    # Only an event with no words at all waits, having nothing to classify.
    description = str(body.get('description') or '').strip()
    if inserted and 'classified_at' not in category_tags and (title or description):
        jobs.enqueue('calendar.classify', id, commit=False)
    # Titled from the typed description for the same reason. A pending job still
    # sees any transcript that lands before it runs, and a title is only ever
    # filled while blank, so this never overrides one typed later.
    if inserted and not title and description:
        jobs.enqueue('calendar.title', id, commit=False)
    db.commit()
    return jsonify({'id': id}), 201


def _clip_count(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --- Event recordings ----------------------------------------------------------
#
# A clip spoken into the event form. The same contract as POST /api/food/recordings:
# the client keeps the audio until this confirms it and replays on reconnect, so
# the id is the client's and a replay is a no-op, checked before the file is read.
#
# The difference is the row the clip belongs to. A meal can be opened by its clip,
# but an event needs a date, so only a client that sends one may open it — the web
# composer, whose clip is rescued by the boot sweep if the composer died before
# Save. The phone's outbox always sends the create first, and sends no date, so a
# clip for an event deleted in the meantime gets a 404 and does not bring it back.

def _transcribe_recording_bg(recording_id: str, event_id: str, path: str,
                             *, now: bool = False) -> None:
    """Transcribe one clip, fold it into the description, then let the model
    finish what the words make possible: the title if it is still blank, and the
    categories if nobody chose them. A failed transcription still classifies
    whatever was typed."""
    if not now:
        jobs.enqueue('calendar.transcribe_recording', recording_id,
                     {'event_id': event_id, 'path': path})
        return

    from backend.routes import stt as stt_routes

    try:
        p = calendar_media.resolve_stored_path(path)
        if p is None or not p.is_file():
            raise RuntimeError('The recording is missing')
        text = stt_routes.transcribe_file(p)
        status, error = 'done', None
    except Exception as e:
        text, status, error = None, 'error', str(e) or 'Failed'
        print(f'Event clip transcription failed for {recording_id}: {e}')

    db = get_db()
    prior = db.execute(
        'SELECT transcript FROM calendar_recordings WHERE id=?', (recording_id,)
    ).fetchone()
    updates = {'transcript_status': status, 'transcript_error': error}
    if text is not None:
        updates['transcript'] = text
    build_update(db, 'calendar_recordings', updates, 'id=?', (recording_id,))
    # Appended once per clip, so a re-run refreshes the clip's own text without
    # pasting it into the event twice.
    if text and not (prior and prior['transcript']):
        _append_description(db, event_id, text, source=recording_id)
    db.commit()

    row = db.execute(
        'SELECT title, description, classified_at FROM calendar_events WHERE id=?',
        (event_id,),
    ).fetchone()
    if row is None:
        return
    title = (row['title'] or '').strip()
    description = (row['description'] or '').strip()
    if description and not title:
        jobs.enqueue('calendar.title', event_id)
    if (title or description) and row['classified_at'] is None:
        jobs.enqueue('calendar.classify', event_id)


def _append_description(db, event_id: str, text: str, *,
                        source: str | None = None) -> None:
    """After anything typed, a blank line between clips, as a meal's note.

    New words are a new thing to classify, so the categories are reopened, as
    a spoken edit reopens them. Unconditionally: categories ticked by hand are
    stamped exactly like the classifier's, so the two cannot be told apart.

    The appended words are speech, naming the clip (`source`) they came from,
    so a re-transcription can replace exactly them; what was typed before them
    keeps its author."""
    row = db.execute(
        'SELECT description FROM calendar_events WHERE id=?', (event_id,)
    ).fetchone()
    if row is None:
        return
    existing = (row['description'] or '').strip()
    merged = f'{existing}\n\n{text}' if existing else text
    authorship.record_change(db, 'calendar_events', event_id, 'description', merged,
                             author=authorship.SPEECH, source=source)
    db.execute(
        'UPDATE calendar_events SET description=?, classified_at=NULL,'
        ' classification_error=NULL WHERE id=?',
        (merged, event_id),
    )


@bp.post('/recordings')
def create_recording():
    audio = request.files.get('audio')
    if audio is None or not audio.filename:
        return jsonify({'error': 'Missing audio file'}), 400
    event_id = (request.form.get('id') or '').strip()
    recording_id = (request.form.get('mediaId') or '').strip()
    if not event_id or not recording_id:
        return jsonify({'error': 'id and mediaId are required'}), 400

    db = get_db()
    existing = db.execute(
        'SELECT id, transcript_status FROM calendar_recordings WHERE id=?',
        (recording_id,),
    ).fetchone()
    if existing:
        return jsonify({'id': event_id, 'recording': dict(existing)}), 201

    ext = calendar_media.resolve_ext(audio.mimetype, audio.filename)
    path = ext and calendar_media.recording_path(event_id, recording_id, ext)
    if not path:
        return jsonify({'error': 'Unsupported audio type'}), 400

    if not db.execute('SELECT 1 FROM calendar_events WHERE id=?', (event_id,)).fetchone():
        date = request.form.get('date')
        if not date or not _valid_date(date):
            return jsonify({'error': 'Not found'}), 404
        db.execute(
            'INSERT INTO calendar_events(id, title, date, created_at) VALUES (?,?,?,?)',
            (event_id, '', date, int(time.time())),
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    audio.save(path)
    db.execute(
        'INSERT INTO calendar_recordings(id, event_id, path, mime, transcript_status,'
        ' created_at) VALUES (?,?,?,?,?,?)',
        (recording_id, event_id, str(path), audio.mimetype, 'running', int(time.time())),
    )
    db.commit()
    _transcribe_recording_bg(recording_id, event_id, str(path))
    return jsonify({
        'id': event_id,
        'recording': {'id': recording_id, 'transcript_status': 'running'},
    }), 201


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
        # tags_json, not a raw dumps: trimmed, lowercased and deduped, so
        # "Work" and "work " are one tag here exactly as they are everywhere
        # else. It also stores an emptied list as NULL rather than '[]'.
        updates['tags'] = tags_json(body['tags'])
    category_tags, err = _category_tags_field(body)
    if err:
        return {}, err
    updates.update(category_tags)
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
    authorship.record_edits(db, 'calendar_events', id, updates)
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
    calendar_media.delete_event_dir(id)
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
        calendar_media.delete_event_dir(id)
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
# A client-minted id, as the device's offline queue mints them.
_ULID_RE = re.compile(r'[0-9A-HJKMNP-TV-Z]{26}')

_SPLIT_COLUMNS = (
    'title', 'description', 'time', 'end_time', 'all_day', 'tags',
    'repeat_freq', 'repeat_interval', 'repeat_byweekday', 'repeat_until',
    # The categories travel with the series: a split that dropped them left
    # the new half uncoloured, and a categoryTags sent with the edit was lost.
    'category_tags', 'classified_at', 'classification_error',
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
    # A client-chosen id for the new series makes an offline-queued split
    # replay idempotently: a replay whose split already landed would otherwise
    # cut the capped series again and start a duplicate.
    new_id = body.get('newId')
    if new_id is not None:
        if not isinstance(new_id, str) or not _ULID_RE.fullmatch(new_id):
            return jsonify({'error': 'newId must be a ULID'}), 400
        if db.execute('SELECT 1 FROM calendar_events WHERE id=?', (new_id,)).fetchone():
            return jsonify({'id': new_id, 'split': True})
    updates, err = _event_updates(body)
    if err:
        return jsonify({'error': err}), 400
    if not updates:
        return jsonify({'id': id, 'split': False})

    cutoff = _cap_cutoff(row, date)
    if row['repeat_freq'] is None or cutoff is None:
        # A one-off, or a cut with no earlier occurrences: nothing to preserve,
        # so edit in place rather than leaving an empty husk of a series behind.
        authorship.record_edits(db, 'calendar_events', id, updates)
        build_update(db, 'calendar_events', updates, 'id=?', (id,))
        db.commit()
        return jsonify({'id': id, 'split': False})

    values = {col: row[col] for col in _SPLIT_COLUMNS}
    values.update({k: v for k, v in updates.items() if k in _SPLIT_COLUMNS})

    new_id = new_id or str(ULID())
    cols = ', '.join(_SPLIT_COLUMNS)
    ph = ','.join('?' * len(_SPLIT_COLUMNS))
    db.execute(
        f'INSERT INTO calendar_events(id, date, created_at, split_from, {cols}) '
        f'VALUES (?,?,?,?,{ph})',
        (new_id, date, int(time.time()), id, *(values[c] for c in _SPLIT_COLUMNS)),
    )
    # The new series starts from the old one's words, and whatever this edit
    # changed in them is the user's.
    for field in ('title', 'description'):
        authorship.carry(db, 'calendar_events', id, new_id, field, values[field])
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
