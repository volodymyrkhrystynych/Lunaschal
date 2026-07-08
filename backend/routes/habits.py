import time
from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.habit_stats import compute_stats

bp = Blueprint('habits', __name__, url_prefix='/api/habits')

MAX_CHECKS_RANGE_DAYS = 400

HABIT_COLS = '''id, name, type, target_value, unit, schedule_type, schedule_days,
                times_per_week, color, position, archived, created_at, updated_at'''


def _parse_schedule_days(csv: str | None) -> list[int] | None:
    if not csv:
        return None
    return [int(x) for x in csv.split(',')]


def _validate(data: dict) -> str | None:
    """Validate a fully-merged habit dict (snake_case keys). Returns an error or None."""
    if not (data.get('name') or '').strip():
        return 'name required'
    if data['type'] not in ('boolean', 'quantity'):
        return 'type must be boolean or quantity'
    if data['type'] == 'quantity':
        target = data.get('target_value')
        if not isinstance(target, (int, float)) or target <= 0:
            return 'quantity habits need a targetValue > 0'
    if data['schedule_type'] not in ('daily', 'weekdays', 'per_week'):
        return 'scheduleType must be daily, weekdays, or per_week'
    if data['schedule_type'] == 'weekdays':
        days = data.get('schedule_days')
        if not days or not isinstance(days, list) or not all(
            isinstance(d, int) and 0 <= d <= 6 for d in days
        ):
            return 'weekdays schedule needs scheduleDays (ints 0=Mon..6=Sun)'
    if data['schedule_type'] == 'per_week':
        tpw = data.get('times_per_week')
        if not isinstance(tpw, int) or not 1 <= tpw <= 7:
            return 'per_week schedule needs timesPerWeek between 1 and 7'
    return None


def _stats_input(row) -> dict:
    return {
        'type': row['type'],
        'target_value': row['target_value'],
        'schedule_type': row['schedule_type'],
        'schedule_days': _parse_schedule_days(row['schedule_days']),
        'times_per_week': row['times_per_week'],
        'created': date.fromtimestamp(row['created_at']),
    }


def _habit_json(row, checks: list[dict], today: date) -> dict:
    d = row_to_dict(row)
    d['scheduleDays'] = _parse_schedule_days(row['schedule_days'])
    d['archived'] = bool(row['archived'])
    stats = compute_stats(_stats_input(row), checks, today)
    d.update({
        'currentStreak': stats['current_streak'],
        'longestStreak': stats['longest_streak'],
        'streakUnit': stats['streak_unit'],
        'completion30': stats['completion_30'],
        'todayStatus': stats['today_status'],
        'todayValue': stats['today_value'],
        'todaySatisfied': stats['today_satisfied'],
        'todayScheduled': stats['today_scheduled'],
    })
    return d


@bp.get('')
def list_habits():
    include_archived = request.args.get('archived') == '1'
    db = get_db()
    where = '' if include_archived else 'WHERE archived = 0'
    rows = db.execute(f'SELECT {HABIT_COLS} FROM habits {where} ORDER BY position').fetchall()

    checks_by_habit: dict[str, list[dict]] = {}
    for c in db.execute('SELECT habit_id, date, status, value FROM habit_checks').fetchall():
        checks_by_habit.setdefault(c['habit_id'], []).append({
            'date': date.fromisoformat(c['date']),
            'status': c['status'],
            'value': c['value'],
        })

    today = date.today()
    return jsonify([_habit_json(r, checks_by_habit.get(r['id'], []), today) for r in rows])


def _body_to_fields(body: dict) -> dict:
    """Map camelCase JSON fields onto snake_case columns (only keys present in body)."""
    mapping = {
        'name': 'name', 'type': 'type', 'targetValue': 'target_value', 'unit': 'unit',
        'scheduleType': 'schedule_type', 'scheduleDays': 'schedule_days',
        'timesPerWeek': 'times_per_week', 'color': 'color',
    }
    return {col: body[key] for key, col in mapping.items() if key in body}


@bp.post('')
def create_habit():
    body = request.json or {}
    data = {
        'name': '', 'type': 'boolean', 'target_value': None, 'unit': None,
        'schedule_type': 'daily', 'schedule_days': None, 'times_per_week': None,
        'color': None,
    }
    data.update(_body_to_fields(body))
    error = _validate(data)
    if error:
        return jsonify({'error': error}), 400

    db = get_db()
    position = db.execute('SELECT COUNT(*) FROM habits').fetchone()[0] + 1
    now = int(time.time())
    habit_id = str(ULID())
    schedule_days = data['schedule_days']
    db.execute(
        '''INSERT INTO habits(id, name, type, target_value, unit, schedule_type,
                              schedule_days, times_per_week, color, position, archived,
                              created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)''',
        (habit_id, data['name'].strip(), data['type'], data['target_value'], data['unit'],
         data['schedule_type'],
         ','.join(str(d) for d in schedule_days) if schedule_days else None,
         data['times_per_week'], data['color'], position, now, now),
    )
    db.commit()
    return jsonify({'id': habit_id}), 201


@bp.patch('/<habit_id>')
def update_habit(habit_id):
    body = request.json or {}
    db = get_db()
    row = db.execute(f'SELECT {HABIT_COLS} FROM habits WHERE id=?', (habit_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    data = {
        'name': row['name'], 'type': row['type'], 'target_value': row['target_value'],
        'unit': row['unit'], 'schedule_type': row['schedule_type'],
        'schedule_days': _parse_schedule_days(row['schedule_days']),
        'times_per_week': row['times_per_week'], 'color': row['color'],
    }
    data.update(_body_to_fields(body))
    error = _validate(data)
    if error:
        return jsonify({'error': error}), 400

    archived = row['archived']
    if 'archived' in body:
        archived = 1 if body['archived'] else 0

    schedule_days = data['schedule_days']
    db.execute(
        '''UPDATE habits SET name=?, type=?, target_value=?, unit=?, schedule_type=?,
                             schedule_days=?, times_per_week=?, color=?, archived=?,
                             updated_at=?
           WHERE id=?''',
        (data['name'].strip(), data['type'], data['target_value'], data['unit'],
         data['schedule_type'],
         ','.join(str(d) for d in schedule_days) if schedule_days else None,
         data['times_per_week'], data['color'], archived, int(time.time()), habit_id),
    )
    db.commit()
    return jsonify({'success': True})


@bp.post('/reorder')
def reorder_habits():
    body = request.json or {}
    order = body.get('order', [])
    if not isinstance(order, list):
        return jsonify({'error': 'order must be a list of ids'}), 400

    db = get_db()
    now = int(time.time())
    for i, habit_id in enumerate(order):
        db.execute(
            'UPDATE habits SET position=?, updated_at=? WHERE id=?',
            (i + 1, now, habit_id),
        )
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<habit_id>')
def delete_habit(habit_id):
    db = get_db()
    row = db.execute('SELECT position FROM habits WHERE id=?', (habit_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    db.execute('DELETE FROM habits WHERE id=?', (habit_id,))
    db.execute(
        'UPDATE habits SET position=position-1, updated_at=? WHERE position > ?',
        (int(time.time()), row['position']),
    )
    db.commit()
    return jsonify({'success': True})


@bp.put('/<habit_id>/checks/<date_str>')
def set_check(habit_id, date_str):
    body = request.json or {}
    status = body.get('status')
    if status not in ('done', 'skipped', 'none'):
        return jsonify({'error': "status must be done, skipped, or none"}), 400
    try:
        check_date = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({'error': 'invalid date'}), 400
    if check_date > date.today():
        return jsonify({'error': 'cannot check a future date'}), 400

    db = get_db()
    habit = db.execute('SELECT id, type FROM habits WHERE id=?', (habit_id,)).fetchone()
    if not habit:
        return jsonify({'error': 'Not found'}), 404

    value = None
    if habit['type'] == 'quantity' and status == 'done':
        value = body.get('value')
        if not isinstance(value, (int, float)):
            return jsonify({'error': 'quantity habits need a value'}), 400
        if value <= 0:
            status = 'none'  # clearing the progress removes the check

    if status == 'none':
        db.execute('DELETE FROM habit_checks WHERE habit_id=? AND date=?',
                   (habit_id, date_str))
    else:
        db.execute(
            '''INSERT INTO habit_checks(id, habit_id, date, status, value, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(habit_id, date) DO UPDATE SET status=excluded.status,
                                                         value=excluded.value''',
            (str(ULID()), habit_id, date_str, status, value, int(time.time())),
        )
    db.commit()
    return jsonify({'success': True})


@bp.get('/checks')
def list_checks():
    try:
        start = date.fromisoformat(request.args.get('from', ''))
        end = date.fromisoformat(request.args.get('to', ''))
    except ValueError:
        return jsonify({'error': 'from and to dates required (YYYY-MM-DD)'}), 400
    if end < start:
        return jsonify({'error': 'to must not be before from'}), 400
    if (end - start).days > MAX_CHECKS_RANGE_DAYS:
        start = end - timedelta(days=MAX_CHECKS_RANGE_DAYS)

    db = get_db()
    rows = db.execute(
        '''SELECT habit_id, date, status, value FROM habit_checks
           WHERE date >= ? AND date <= ? ORDER BY date''',
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])
