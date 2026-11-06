"""The coach's settings, read fresh every time they are used.

The spoken task nudge this replaces read its switch once at startup, so turning
it off did nothing until a restart — one of the reasons it was removed. Nothing
here is cached.
"""
import json

DEFAULT_BEHAVIORS = {'weight': True, 'calories': True, 'habits': True}


def _row(db) -> dict:
    row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
    return dict(row) if row else {}


def behaviors(db) -> dict:
    """Which kinds of thing count toward a day and may be reminded about."""
    raw = _row(db).get('coach_behaviors')
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        parsed = {}
    return {k: bool(parsed.get(k, v)) for k, v in DEFAULT_BEHAVIORS.items()}


def checkin(db) -> tuple[bool, int]:
    row = _row(db)
    hour = row.get('coach_checkin_hour')
    return bool(row.get('coach_checkin_enabled')), 21 if hour is None else int(hour)


def briefing_hour(db) -> int:
    hour = _row(db).get('briefing_hour')
    return 5 if hour is None else int(hour)


def engine(db) -> dict:
    """The reminder engine's switches, with the shipped defaults for any
    column an old database doesn't have yet."""
    row = _row(db)

    def num(key, default, cast=int):
        value = row.get(key)
        return default if value is None else cast(value)

    return {
        'enabled': bool(row.get('coach_enabled')),
        'quiet_start': num('coach_quiet_start', 22),
        'quiet_end': num('coach_quiet_end', 8),
        'daily_cap': num('coach_daily_cap', 3),
        'no_send_rate': num('coach_no_send_rate', 0.25, float),
    }


SETTINGS = {
    # API name: (column, type, (min, max) or None)
    'enabled': ('coach_enabled', bool, None),
    'checkinEnabled': ('coach_checkin_enabled', bool, None),
    'checkinHour': ('coach_checkin_hour', int, (0, 23)),
    'quietStart': ('coach_quiet_start', int, (0, 23)),
    'quietEnd': ('coach_quiet_end', int, (0, 23)),
    'dailyCap': ('coach_daily_cap', int, (0, 12)),
    'noSendRate': ('coach_no_send_rate', float, (0.1, 0.5)),
}


def read_all(db) -> dict:
    enabled, hour = checkin(db)
    eng = engine(db)
    return {'enabled': eng['enabled'], 'checkinEnabled': enabled, 'checkinHour': hour,
            'quietStart': eng['quiet_start'], 'quietEnd': eng['quiet_end'], 'dailyCap': eng['daily_cap'],
            'noSendRate': eng['no_send_rate'], 'behaviors': behaviors(db)}


def update(db, changes: dict) -> dict:
    """Validate and write a partial settings change. The no-send rate has a
    floor: below it the coach can no longer tell what its reminders caused."""
    for key, value in changes.items():
        if key == 'behaviors':
            if not isinstance(value, dict) or not all(isinstance(v, bool) for v in value.values()):
                raise ValueError('behaviors must map names to true/false')
            merged = behaviors(db) | {k: v for k, v in value.items() if k in DEFAULT_BEHAVIORS}
            db.execute('UPDATE settings SET coach_behaviors=? WHERE id=1', (json.dumps(merged),))
            continue
        if key not in SETTINGS:
            raise ValueError(f'Unknown coach setting {key!r}')
        column, kind, bounds = SETTINGS[key]
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f'{key} must be true or false')
            value = int(value)
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f'{key} must be a number')
            value = kind(value)
            if not bounds[0] <= value <= bounds[1]:
                raise ValueError(f'{key} must be between {bounds[0]} and {bounds[1]}')
        db.execute(f'UPDATE settings SET {column}=? WHERE id=1', (value,))
    db.commit()
    return read_all(db)
